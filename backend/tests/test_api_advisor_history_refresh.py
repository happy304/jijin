"""历史交易建议刷新接口测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import io
from pathlib import Path
from textwrap import dedent
from unittest.mock import AsyncMock, patch

import pandas as pd

from app.services.advisor_backtest import run_advisor_backtest, run_walk_forward_validation
from app.api.v1.advisor import PositionDetailPayload
from app.tasks.advisor import refresh_oos_validation_cache
from app.tasks.schedule import BEAT_SCHEDULE
from app.services.advisor_execution import AdvisorExecutionRequest, build_execution_bundle
from app.services.advisor_feedback import AdvisorFeedbackLearner, FeedbackConfig
from app.services.advisor_user_learning import AdvisorUserLearningService
from app.services.advisor_parameter_governance import evaluate_parameter_gate
from app.services.advisor_oos import OOSValidationSnapshot, OOSValidationStore
from app.services.advisor_tracking import compute_engine_health_async
from app.services.cross_sectional_scorer import load_fund_data_for_scoring
from app.services.macro_factor import load_macro_data
from app.services.trading_advisor import AdviceReasoning, AdviceValidity, AdvisorConfig, DecisionAudit, FundTradingRules, PortfolioImpact, ProfileConstraint, ReliabilityAdjustment, RiskBudgetPosition, SuitabilityCheck, TradePlan, TradingAdvice, TradingAdvisor, apply_fund_trading_rules, build_advisor_overfit_risk, calculate_fund_trade_timing, load_fund_trading_rules, load_nav_data_for_advisor, load_strategy_signals_for_advisor, normalize_trade_direction

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.data.models import Base
from app.data.models.advisor_execution_records import AdvisorExecutionRecord
from app.data.models.advisor_learned_params_versions import AdvisorLearnedParamsVersion
from app.data.models.advisor_position_imports import AdvisorPositionImport
from app.data.models.advisor_positions import AdvisorPosition
from app.data.models.advisor_results import AdvisorResult
from app.data.models.advisor_oos_snapshots import AdvisorOOSSnapshot
from app.data.models.advisor_parameter_sets import AdvisorParameterSet
from app.data.models.advisor_reminders import AdvisorReminder
from app.data.models.benchmark import BenchmarkNav
from app.data.models.fund_dividends import FundDividend
from app.data.models.fund_meta_history import FundMetaHistory
from app.data.models.fund_nav import FundNav
from app.data.models.funds import Fund
from app.data.models.index_valuation import IndexValuation
from app.data.models.signals import Signal
from app.data.providers.snapshot import SnapshotArchive
from app.data.session import get_session
from app.main import create_app
import app.data.providers.snapshot as snapshot_module


@pytest.fixture
def test_settings() -> Settings:
    get_settings.cache_clear()
    return Settings(
        APP_ENV="test",
        DEBUG="true",
        LOG_LEVEL="WARNING",
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        DB_AUTO_MIGRATE="false",
        PROMETHEUS_ENABLED="false",
        REDIS_URL="redis://localhost:6379/15",
    )


@pytest.fixture
async def app(test_settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(test_settings)
    application.dependency_overrides[get_settings] = lambda: test_settings

    engine = create_async_engine(test_settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    application.dependency_overrides[get_session] = override_get_session
    try:
        yield application
    finally:
        application.dependency_overrides.clear()
        await engine.dispose()
        get_settings.cache_clear()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _insert_history_record(app: FastAPI, **overrides) -> int:
    session_provider = app.dependency_overrides[get_session]
    payload = {
        "advice_date": date(2026, 5, 20),
        "fund_codes": ["000001", "000002"],
        "total_capital": Decimal("1000.00"),
        "risk_level": "moderate",
        "strategy_id": None,
        "strategy_name": None,
        "current_positions": {"000001": 200},
        "positions_detail": {"000001": {"amount": 200, "buy_date": "2026-05-01", "cost": 180}},
        "advices": [{"fund_code": "000001", "action": "hold"}],
        "summary": {"buy_count": 0, "sell_count": 0, "hold_count": 1, "total_buy_amount": 0, "total_sell_amount": 0, "high_confidence_signals": 0, "top_buy": None, "top_sell": None},
        "note": "old",
        "tracked_returns": {"000001": {"return_20d": 0.1}},
        "tracked_at": datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc),
        "created_at": datetime(2026, 5, 21, 10, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 5, 21, 10, 0, tzinfo=timezone.utc),
        "user_profile": {"risk_level": "moderate"},
    }
    payload.update(overrides)

    async for session in session_provider():
        row = AdvisorResult(**payload)
        session.add(row)
        await session.flush()
        row_id = row.id
        await session.commit()
        return row_id
    raise AssertionError("无法创建测试历史记录")


@pytest.mark.asyncio
async def test_advisor_user_learning_profile_learns_execution_pacing(client: AsyncClient, app: FastAPI):
    result_id = await _insert_history_record(
        app,
        fund_codes=["000001"],
        advices=[{
            "fund_code": "000001",
            "action": "buy",
            "suggested_amount": 1000,
            "suggested_pct": 0.1,
            "confidence": 0.7,
        }],
    )

    session_provider = app.dependency_overrides[get_session]
    async for session in session_provider():
        for idx in range(5):
            session.add(AdvisorExecutionRecord(
                advisor_result_id=result_id,
                advice_date=date(2026, 5, 20),
                fund_code="000001",
                advice_action="buy",
                trade_intent="subscribe",
                suggested_amount=1000,
                suggested_pct=0.1,
                confidence=0.7,
                execution_status="partial" if idx < 3 else "executed",
                executed_date=date(2026, 5, 23 + idx),
                executed_amount=400,
                source="test",
            ))
        await session.commit()
        break

    resp = await client.get('/api/v1/advisor/user-learning/profile?refresh=true')

    assert resp.status_code == 200, resp.text
    profile = resp.json()['profile']
    assert profile['sample_count'] == 5
    assert profile['confidence'] > 0
    assert profile['preferred_execution_style'] in {'batch', 'small_steps', 'slower_cadence'}
    assert profile['amount_scale'] < 1.0


@pytest.mark.asyncio
async def test_advisor_user_learning_apply_to_profile_adds_audit_payload(app: FastAPI):
    session_provider = app.dependency_overrides[get_session]
    async for session in session_provider():
        snapshot = await AdvisorUserLearningService.learn_and_persist(session)
        enriched = AdvisorUserLearningService.apply_to_user_profile({'risk_level': 'moderate'}, snapshot)
        assert enriched['advisor_personalization']['profile_key'] == 'default'
        assert 'safeguards' in enriched['advisor_personalization']
        break


@pytest.mark.asyncio
async def test_get_advisor_history_exposes_nav_data_stale(client: AsyncClient, app: FastAPI):
    marker = {
        "stale": True,
        "reason": "adj_nav_history_recalculated",
        "fund_codes": ["000001"],
    }
    result_id = await _insert_history_record(
        app,
        fund_codes=["000001"],
        execution_context={"nav_data_stale": marker},
    )

    detail_resp = await client.get(f'/api/v1/advisor/history/{result_id}')
    assert detail_resp.status_code == 200, detail_resp.text
    detail_body = detail_resp.json()
    assert detail_body["nav_data_stale"] == marker
    assert detail_body["execution_context"]["nav_data_stale"] == marker

    list_resp = await client.get('/api/v1/advisor/history')
    assert list_resp.status_code == 200, list_resp.text
    items = list_resp.json()["items"]
    target = next(item for item in items if item["id"] == result_id)
    assert target["nav_data_stale"] == marker


@pytest.mark.asyncio
async def test_get_advisor_history_detail_auto_generates_reminders(client: AsyncClient, app: FastAPI):
    result_id = await _insert_history_record(
        app,
        fund_codes=["000001"],
        advices=[{
            "fund_code": "000001",
            "action": "watch",
            "validity": {"valid_until": "2020-01-01"},
            "data_quality": {"status": "poor"},
            "overfit_risk": {"level": "high"},
            "trade_plan": {"execution_type": "batch"},
        }],
    )

    resp = await client.get(f'/api/v1/advisor/history/{result_id}')

    assert resp.status_code == 200, resp.text
    body = resp.json()
    reminder_types = {item['reminder_type'] for item in body['reminders']}
    assert {
        'validity_expired',
        'poor_quality',
        'overfit_high',
        'batch_plan_present',
        'watch_actions',
    }.issubset(reminder_types)
    assert len(body['reminders']) >= 5

    session_provider = app.dependency_overrides[get_session]
    async for session in session_provider():
        stored = await session.execute(
            select(AdvisorReminder).where(AdvisorReminder.advisor_result_id == result_id)
        )
        assert len(stored.scalars().all()) >= 5
        break


@pytest.mark.asyncio
async def test_refresh_and_update_advisor_reminders_status(client: AsyncClient, app: FastAPI):
    result_id = await _insert_history_record(
        app,
        fund_codes=["000001"],
        advices=[{
            "fund_code": "000001",
            "action": "watch",
            "validity": {"valid_until": "2020-01-01"},
        }],
        tracked_returns=None,
        tracked_at=None,
    )

    refresh_resp = await client.post(f'/api/v1/advisor/reminders/refresh?advisor_result_id={result_id}')
    assert refresh_resp.status_code == 200, refresh_resp.text
    refresh_body = refresh_resp.json()
    assert refresh_body['status'] == 'success'
    assert refresh_body['processed'] == 1
    assert refresh_body['items'][0]['advisor_result_id'] == result_id
    assert refresh_body['items'][0]['active'] >= 2

    list_resp = await client.get(f'/api/v1/advisor/reminders?advisor_result_id={result_id}&status=active')
    assert list_resp.status_code == 200, list_resp.text
    items = list_resp.json()['items']
    assert len(items) >= 2
    target = next(item for item in items if item['reminder_type'] == 'validity_expired')

    patch_resp = await client.patch(
        f"/api/v1/advisor/reminders/{target['id']}",
        json={'status': 'dismissed'},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    patched = patch_resp.json()['item']
    assert patched['status'] == 'dismissed'
    assert patched['dismissed_at'] is not None
    assert patched['resolved_at'] is None

    dismissed_resp = await client.get('/api/v1/advisor/reminders?status=dismissed')
    assert dismissed_resp.status_code == 200, dismissed_resp.text
    dismissed_ids = {item['id'] for item in dismissed_resp.json()['items']}
    assert target['id'] in dismissed_ids


@pytest.mark.asyncio
async def test_advisor_reminder_digest_returns_cross_end_summary(client: AsyncClient, app: FastAPI):
    result_id = await _insert_history_record(
        app,
        fund_codes=["000001"],
        advices=[{
            "fund_code": "000001",
            "action": "buy",
            "validity": {"valid_until": "2020-01-01"},
            "data_quality": {"status": "poor"},
            "trade_plan": {"execution_type": "batch"},
        }],
        tracked_returns=None,
        tracked_at=None,
    )

    refresh_resp = await client.post(f'/api/v1/advisor/reminders/refresh?advisor_result_id={result_id}')
    assert refresh_resp.status_code == 200, refresh_resp.text

    digest_resp = await client.post('/api/v1/advisor/reminders/digest?dry_run=true&days=3&min_severity=warning')

    assert digest_resp.status_code == 200, digest_resp.text
    body = digest_resp.json()
    assert body['status'] == 'dry_run'
    assert body['digest']['notification_ready'] is True
    assert body['digest']['summary']['total'] >= 1
    assert body['digest']['summary']['by_severity']['error'] >= 1
    assert any(item['reminder_type'] == 'validity_expired' for item in body['digest']['items'])
    assert 'Advisor 提醒摘要' in body['message']


@pytest.mark.asyncio
async def test_advisor_reminder_preferences_filter_digest_categories(client: AsyncClient, app: FastAPI):
    result_id = await _insert_history_record(
        app,
        fund_codes=["000001"],
        advices=[{
            "fund_code": "000001",
            "action": "buy",
            "validity": {"valid_until": "2099-01-01"},
            "data_quality": {"status": "poor"},
            "trade_plan": {"execution_type": "batch"},
        }],
        tracked_returns=None,
        tracked_at=None,
    )

    refresh_resp = await client.post(f'/api/v1/advisor/reminders/refresh?advisor_result_id={result_id}')
    assert refresh_resp.status_code == 200, refresh_resp.text

    put_resp = await client.put(
        '/api/v1/advisor/reminders/preferences',
        json={
            'enabled': True,
            'min_severity': 'info',
            'lookahead_days': 30,
            'channels': ['telegram'],
            'muted_categories': ['risk'],
            'quiet_hours': {'start': '22:00', 'end': '08:00', 'timezone': 'Asia/Shanghai'},
        },
    )
    assert put_resp.status_code == 200, put_resp.text
    preference = put_resp.json()['preference']
    assert preference['min_severity'] == 'info'
    assert preference['channels'] == ['telegram']
    assert preference['muted_categories'] == ['risk']

    get_resp = await client.get('/api/v1/advisor/reminders/preferences')
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json()['preference']['lookahead_days'] == 30

    digest_resp = await client.post('/api/v1/advisor/reminders/digest?dry_run=true')
    assert digest_resp.status_code == 200, digest_resp.text
    body = digest_resp.json()
    reminder_types = {item['reminder_type'] for item in body['digest']['items']}
    assert 'poor_quality' not in reminder_types
    assert 'batch_plan_present' in reminder_types
    assert body['digest']['window']['muted_categories'] == ['risk']
    assert body['preference']['channels'] == ['telegram']


@pytest.mark.asyncio
async def test_advisor_reminder_preferences_can_disable_digest(client: AsyncClient):
    put_resp = await client.put(
        '/api/v1/advisor/reminders/preferences',
        json={
            'enabled': False,
            'min_severity': 'warning',
            'lookahead_days': 3,
            'channels': None,
            'muted_categories': [],
            'quiet_hours': None,
        },
    )
    assert put_resp.status_code == 200, put_resp.text

    digest_resp = await client.post('/api/v1/advisor/reminders/digest?dry_run=true')
    assert digest_resp.status_code == 200, digest_resp.text
    body = digest_resp.json()
    assert body['status'] == 'disabled'
    assert body['preference']['enabled'] is False
    assert body['digest'] is None


@pytest.mark.asyncio
async def test_execution_record_create_auto_resolves_execution_missing_reminder(client: AsyncClient, app: FastAPI):
    result_id = await _insert_history_record(
        app,
        fund_codes=["000001"],
        advices=[{
            "fund_code": "000001",
            "action": "buy",
            "suggested_amount": 1200,
            "trade_intent": "subscribe",
        }],
        tracked_returns=None,
        tracked_at=None,
    )

    refresh_resp = await client.post(f'/api/v1/advisor/reminders/refresh?advisor_result_id={result_id}')
    assert refresh_resp.status_code == 200, refresh_resp.text
    reminder_list_resp = await client.get(f'/api/v1/advisor/reminders?advisor_result_id={result_id}&status=active')
    assert reminder_list_resp.status_code == 200, reminder_list_resp.text
    assert any(item['reminder_type'] == 'execution_missing' for item in reminder_list_resp.json()['items'])

    create_resp = await client.post(
        f'/api/v1/advisor/history/{result_id}/executions',
        json={
            'fund_code': '000001',
            'execution_status': 'executed',
            'executed_date': '2026-05-21',
            'executed_amount': 1200,
        },
    )
    assert create_resp.status_code == 200, create_resp.text

    reminder_list_resp = await client.get(f'/api/v1/advisor/reminders?advisor_result_id={result_id}&status=active')
    assert reminder_list_resp.status_code == 200, reminder_list_resp.text
    assert all(item['reminder_type'] != 'execution_missing' for item in reminder_list_resp.json()['items'])

    detail_resp = await client.get(f'/api/v1/advisor/history/{result_id}')
    assert detail_resp.status_code == 200, detail_resp.text
    execution_missing_items = [item for item in detail_resp.json()['reminders'] if item['reminder_type'] == 'execution_missing']
    assert execution_missing_items
    assert all(item['status'] == 'resolved' for item in execution_missing_items)


@pytest.mark.asyncio
async def test_execution_record_delete_reactivates_execution_missing_reminder(client: AsyncClient, app: FastAPI):
    result_id = await _insert_history_record(
        app,
        fund_codes=["000001"],
        advices=[{
            "fund_code": "000001",
            "action": "buy",
            "suggested_amount": 1200,
            "trade_intent": "subscribe",
        }],
        tracked_returns=None,
        tracked_at=None,
    )

    create_resp = await client.post(
        f'/api/v1/advisor/history/{result_id}/executions',
        json={
            'fund_code': '000001',
            'execution_status': 'executed',
            'executed_date': '2026-05-21',
            'executed_amount': 1200,
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    execution_id = create_resp.json()['record']['id']

    delete_resp = await client.delete(f'/api/v1/advisor/executions/{execution_id}')
    assert delete_resp.status_code == 200, delete_resp.text

    reminder_list_resp = await client.get(f'/api/v1/advisor/reminders?advisor_result_id={result_id}&status=active')
    assert reminder_list_resp.status_code == 200, reminder_list_resp.text
    assert any(item['reminder_type'] == 'execution_missing' for item in reminder_list_resp.json()['items'])


@pytest.mark.asyncio
async def test_import_advisor_positions_returns_canonical_rows(client: AsyncClient):
    csv_content = (
        '基金代码,当前市值,持有份额,持仓成本,买入日期\n'
        '000001,10000,8000,9500,2026-05-20\n'
        '000002,abc,1000,800,2026-05-21\n'
        '000003,5000,3000,4500,2026/05/22\n'
    ).encode('utf-8')

    resp = await client.post(
        '/api/v1/advisor/positions/import',
        files={'file': ('positions.csv', csv_content, 'text/csv')},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['status'] == 'partial'
    assert body['imported_count'] == 2
    assert body['failed_count'] == 1
    assert body['positions'][0]['fund_code'] == '000001'
    assert body['positions'][0]['buy_date'] == '2026-05-20'
    assert body['positions'][1]['fund_code'] == '000003'
    assert body['positions'][1]['buy_date'] == '2026-05-22'
    failed_row = next(row for row in body['rows'] if row['status'] == 'failed')
    assert failed_row['row_number'] == 3
    assert failed_row['fund_code'] == '000002'
    assert '无法识别' in failed_row['error']
    assert body['governance_summary']['position_count'] == 2
    assert body['governance_summary']['total_market_value'] == 15000.0


@pytest.mark.asyncio
async def test_import_advisor_positions_reports_governance_summary(client: AsyncClient, app: FastAPI):
    csv_content = (
        '基金代码,当前市值,持有份额,持仓成本,买入日期\n'
        '000001,10000,8000,9500,2026-05-20\n'
        '000001,12000,8100,9600,2026-05-21\n'
        '000004,0,0,0,2026-05-22\n'
        '000005,1000,500,10000,2026-05-23\n'
    ).encode('utf-8')

    resp = await client.post(
        '/api/v1/advisor/positions/import',
        files={'file': ('positions-governance.csv', csv_content, 'text/csv')},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    summary = body['governance_summary']
    assert summary['imported_row_count'] == 4
    assert summary['position_count'] == 3
    assert summary['total_market_value'] == 13000.0
    assert summary['total_cost_basis'] == 19600.0
    assert summary['duplicate_fund_codes'] == ['000001']
    assert summary['zero_value_fund_codes'] == ['000004']
    assert summary['suspicious_cost_fund_codes'] == ['000005']
    assert len(summary['warnings']) == 3

    history_resp = await client.get('/api/v1/advisor/positions/import-history?limit=10')
    assert history_resp.status_code == 200, history_resp.text
    history_item = next(item for item in history_resp.json()['items'] if item['filename'] == 'positions-governance.csv')
    assert history_item['metadata']['governance_summary']['duplicate_fund_codes'] == ['000001']
    assert history_item['metadata']['governance_summary']['zero_value_fund_codes'] == ['000004']

    restore_resp = await client.post(f"/api/v1/advisor/positions/import-history/{history_item['id']}/restore")
    assert restore_resp.status_code == 200, restore_resp.text
    assert restore_resp.json()['restored_from']['metadata']['governance_summary']['suspicious_cost_fund_codes'] == ['000005']

    async for session in app.dependency_overrides[get_session]():
        rows = (await session.execute(select(AdvisorPosition).order_by(AdvisorPosition.fund_code.asc()))).scalars().all()
        assert rows[0].metadata_json['governance_summary']['duplicate_fund_codes'] == ['000001']
        break


@pytest.mark.asyncio
async def test_import_advisor_positions_rejects_empty_file(client: AsyncClient):
    resp = await client.post(
        '/api/v1/advisor/positions/import',
        files={'file': ('positions.csv', b'', 'text/csv')},
    )

    assert resp.status_code == 400
    assert '文件' in resp.text or 'empty' in resp.text.lower()


@pytest.mark.asyncio
async def test_import_advisor_positions_supports_excel(client: AsyncClient):
    frame = pd.DataFrame([
        {"基金代码": "000001", "当前市值": 10000, "持有份额": 8000, "持仓成本": 9500, "买入日期": "2026-05-20"},
        {"基金代码": "000003", "当前市值": 5000, "持有份额": 3000, "持仓成本": 4500, "买入日期": "2026/05/22"},
    ])
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        frame.to_excel(writer, index=False, sheet_name='持仓')

    resp = await client.post(
        '/api/v1/advisor/positions/import',
        files={'file': ('positions.xlsx', buffer.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['status'] == 'completed'
    assert body['imported_count'] == 2
    assert body['positions'][1]['buy_date'] == '2026-05-22'


@pytest.mark.asyncio
async def test_download_advisor_positions_templates(client: AsyncClient):
    csv_resp = await client.get('/api/v1/advisor/positions/template?format=csv')
    assert csv_resp.status_code == 200
    assert '基金代码' in csv_resp.content.decode('utf-8-sig')
    assert 'attachment; filename="advisor_positions_template.csv"' == csv_resp.headers['content-disposition']

    xlsx_resp = await client.get('/api/v1/advisor/positions/template?format=xlsx')
    assert xlsx_resp.status_code == 200
    assert xlsx_resp.headers['content-type'].startswith('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    assert xlsx_resp.headers['content-disposition'] == 'attachment; filename="advisor_positions_template.xlsx"'
    parsed = pd.read_excel(io.BytesIO(xlsx_resp.content))
    assert str(parsed.iloc[0]['基金代码']).zfill(6) == '000001'


@pytest.mark.asyncio
async def test_list_advisor_position_import_history_returns_latest_first(client: AsyncClient, app: FastAPI):
    first_csv = (
        '基金代码,当前市值,持有份额,持仓成本,买入日期\n'
        '000001,10000,8000,9500,2026-05-20\n'
    ).encode('utf-8')
    second_csv = (
        '基金代码,当前市值,持有份额,持仓成本,买入日期\n'
        '000003,5000,3000,4500,2026-05-22\n'
    ).encode('utf-8')

    first_resp = await client.post(
        '/api/v1/advisor/positions/import',
        files={'file': ('first.csv', first_csv, 'text/csv')},
    )
    assert first_resp.status_code == 200, first_resp.text

    second_resp = await client.post(
        '/api/v1/advisor/positions/import',
        files={'file': ('second.csv', second_csv, 'text/csv')},
    )
    assert second_resp.status_code == 200, second_resp.text

    history_resp = await client.get('/api/v1/advisor/positions/import-history?limit=10')
    assert history_resp.status_code == 200, history_resp.text
    body = history_resp.json()
    assert body['total'] >= 2
    assert body['page'] == 1
    assert body['page_size'] == 10
    assert body['pages'] >= 1
    assert body['items'][0]['filename'] == 'second.csv'
    assert body['items'][1]['filename'] == 'first.csv'

    async for session in app.dependency_overrides[get_session]():
        rows = (await session.execute(select(AdvisorPositionImport).order_by(AdvisorPositionImport.id.asc()))).scalars().all()
        assert len(rows) >= 2
        assert rows[-1].filename == 'second.csv'
        break


@pytest.mark.asyncio
async def test_list_advisor_position_import_history_supports_pagination(client: AsyncClient):
    for name in ('first.csv', 'second.csv', 'third.csv'):
        csv_content = (
            '基金代码,当前市值,持有份额,持仓成本,买入日期\n'
            '000001,10000,8000,9500,2026-05-20\n'
        ).encode('utf-8')
        resp = await client.post(
            '/api/v1/advisor/positions/import',
            files={'file': (name, csv_content, 'text/csv')},
        )
        assert resp.status_code == 200, resp.text

    page_1_resp = await client.get('/api/v1/advisor/positions/import-history?page=1&page_size=2')
    assert page_1_resp.status_code == 200, page_1_resp.text
    page_1 = page_1_resp.json()
    assert page_1['total'] >= 3
    assert page_1['page'] == 1
    assert page_1['page_size'] == 2
    assert page_1['pages'] >= 2
    assert len(page_1['items']) == 2
    assert page_1['items'][0]['filename'] == 'third.csv'
    assert page_1['items'][1]['filename'] == 'second.csv'

    page_2_resp = await client.get('/api/v1/advisor/positions/import-history?page=2&page_size=2')
    assert page_2_resp.status_code == 200, page_2_resp.text
    page_2 = page_2_resp.json()
    assert page_2['page'] == 2
    assert page_2['page_size'] == 2
    assert len(page_2['items']) >= 1
    assert page_2['items'][0]['filename'] == 'first.csv'


@pytest.mark.asyncio
async def test_restore_advisor_positions_from_import_history_replaces_current_snapshot(client: AsyncClient, app: FastAPI):
    first_csv = (
        '基金代码,当前市值,持有份额,持仓成本,买入日期\n'
        '000001,10000,8000,9500,2026-05-20\n'
        '000003,5000,3000,4500,2026-05-22\n'
    ).encode('utf-8')
    second_csv = (
        '基金代码,当前市值,持有份额,持仓成本,买入日期\n'
        '000009,9000,6000,8800,2026-05-25\n'
    ).encode('utf-8')

    first_resp = await client.post(
        '/api/v1/advisor/positions/import',
        files={'file': ('first.csv', first_csv, 'text/csv')},
    )
    assert first_resp.status_code == 200, first_resp.text

    second_resp = await client.post(
        '/api/v1/advisor/positions/import',
        files={'file': ('second.csv', second_csv, 'text/csv')},
    )
    assert second_resp.status_code == 200, second_resp.text

    history_resp = await client.get('/api/v1/advisor/positions/import-history?limit=10')
    assert history_resp.status_code == 200, history_resp.text
    history_items = history_resp.json()['items']
    restore_target = next(item for item in history_items if item['filename'] == 'first.csv')
    assert restore_target['positions'][0]['fund_code'] == '000001'

    restore_resp = await client.post(f"/api/v1/advisor/positions/import-history/{restore_target['id']}/restore")
    assert restore_resp.status_code == 200, restore_resp.text
    restore_body = restore_resp.json()
    assert restore_body['status'] == 'restored'
    assert restore_body['total'] == 2
    assert restore_body['restored_from']['filename'] == 'first.csv'
    assert [item['fund_code'] for item in restore_body['positions']] == ['000001', '000003']

    current_resp = await client.get('/api/v1/advisor/positions')
    assert current_resp.status_code == 200, current_resp.text
    current_body = current_resp.json()
    assert [item['fund_code'] for item in current_body['positions']] == ['000001', '000003']

    async for session in app.dependency_overrides[get_session]():
        rows = (await session.execute(select(AdvisorPosition).order_by(AdvisorPosition.fund_code.asc()))).scalars().all()
        assert [row.fund_code for row in rows] == ['000001', '000003']
        assert rows[0].source == 'import_restore'
        assert rows[0].metadata_json['restored_from_filename'] == 'first.csv'
        assert rows[0].metadata_json['restored_from_import_id'] == restore_target['id']
        break


@pytest.mark.asyncio
async def test_restore_advisor_positions_from_import_history_returns_404_for_missing_record(client: AsyncClient):
    resp = await client.post('/api/v1/advisor/positions/import-history/999/restore')
    assert resp.status_code == 404
    assert '导入历史不存在' in resp.text


@pytest.mark.asyncio
async def test_import_advisor_positions_persists_successful_rows(client: AsyncClient, app: FastAPI):
    seed_resp = await client.put(
        '/api/v1/advisor/positions',
        json={
            'positions': [
                {
                    'fund_code': '009999',
                    'market_value': 1,
                    'shares': 1,
                    'cost_basis': 1,
                    'buy_date': '2026-05-01',
                }
            ]
        },
    )
    assert seed_resp.status_code == 200, seed_resp.text

    csv_content = (
        '基金代码,当前市值,持有份额,持仓成本,买入日期\n'
        '000001,10000,8000,9500,2026-05-20\n'
        '000002,abc,1000,800,2026-05-21\n'
        '000003,5000,3000,4500,2026/05/22\n'
    ).encode('utf-8')

    resp = await client.post(
        '/api/v1/advisor/positions/import',
        files={'file': ('positions.csv', csv_content, 'text/csv')},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['imported_count'] == 2

    list_resp = await client.get('/api/v1/advisor/positions')
    assert list_resp.status_code == 200, list_resp.text
    list_body = list_resp.json()
    assert list_body['total'] == 2
    assert [item['fund_code'] for item in list_body['positions']] == ['000001', '000003']
    assert list_body['positions'][1]['buy_date'] == '2026-05-22'

    async for session in app.dependency_overrides[get_session]():
        rows = (await session.execute(select(AdvisorPosition).order_by(AdvisorPosition.fund_code.asc()))).scalars().all()
        assert [row.fund_code for row in rows] == ['000001', '000003']
        assert rows[0].source == 'import'
        assert rows[0].metadata_json['import_filename'] == 'positions.csv'

        history_rows = (await session.execute(select(AdvisorPositionImport).order_by(AdvisorPositionImport.id.asc()))).scalars().all()
        assert len(history_rows) == 1
        assert history_rows[0].filename == 'positions.csv'
        assert history_rows[0].file_format == 'csv'
        assert history_rows[0].status == 'partial'
        assert history_rows[0].imported_count == 2
        assert history_rows[0].failed_count == 1
        assert history_rows[0].replaced_position_count == 2
        assert history_rows[0].rows_json[1]['status'] == 'failed'
        break


@pytest.mark.asyncio
async def test_replace_advisor_positions_persists_manual_snapshot(client: AsyncClient, app: FastAPI):
    resp = await client.put(
        '/api/v1/advisor/positions',
        json={
            'positions': [
                {
                    'fund_code': '000002',
                    'market_value': 23000,
                    'shares': 12000,
                    'cost_basis': 21000,
                    'buy_date': '2026-05-02',
                },
                {
                    'fund_code': '000001',
                    'market_value': 12000,
                    'shares': 9000,
                    'cost_basis': 11000,
                    'buy_date': None,
                },
            ]
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['status'] == 'saved'
    assert body['total'] == 2
    assert [item['fund_code'] for item in body['positions']] == ['000001', '000002']

    list_resp = await client.get('/api/v1/advisor/positions')
    assert list_resp.status_code == 200, list_resp.text
    list_body = list_resp.json()
    assert list_body['total'] == 2
    assert list_body['positions'][0]['market_value'] == 12000.0

    async for session in app.dependency_overrides[get_session]():
        rows = (await session.execute(select(AdvisorPosition).order_by(AdvisorPosition.fund_code.asc()))).scalars().all()
        assert len(rows) == 2
        assert rows[0].source == 'manual'
        assert rows[0].metadata_json['saved_via'] == 'positions_api'
        break


@pytest.mark.asyncio
async def test_refresh_history_recomputes_manual_record_and_clears_tracking(client: AsyncClient, app: FastAPI):
    result_id = await _insert_history_record(app)

    refreshed_response = {
        "advice_date": "2026-05-23",
        "total_capital": 1000,
        "risk_level": "moderate",
        "fund_count": 2,
        "advices": [
            {"fund_code": "000002", "action": "buy", "trade_intent": "subscribe", "confidence": 0.7},
            {"fund_code": "000001", "action": "sell", "trade_intent": "redeem", "confidence": 0.8},
        ],
        "summary": {
            "buy_count": 1,
            "sell_count": 1,
            "hold_count": 0,
            "total_buy_amount": 500,
            "total_sell_amount": 200,
            "high_confidence_signals": 2,
            "top_buy": "000002",
            "top_sell": "000001",
        },
        "trading_time": {"effective_date": "2026-05-23", "cutoff_info": "ok", "note": "ok"},
        "disclaimer": "test",
    }

    advice_buy = TradingAdvice(
        fund_code="000002",
        fund_name="测试基金2",
        fund_type="stock",
        advice_date="2026-05-23",
    )
    advice_buy.action = "buy"
    advice_buy.confidence = 0.7
    advice_buy.suggested_amount = 500

    advice_sell = TradingAdvice(
        fund_code="000001",
        fund_name="测试基金1",
        fund_type="stock",
        advice_date="2026-05-23",
    )
    advice_sell.action = "sell"
    advice_sell.confidence = 0.8
    advice_sell.suggested_amount = 200

    bundle = type(
        "Bundle",
        (),
        {
            "nav_data": {"000001": [("2026-05-23", 1.0)]},
            "learned_weights": type(
                "Learned",
                (),
                {
                    "version_id": 42,
                    "learn_date": "2026-05-20",
                    "engine_version": "5.0",
                    "confidence": 0.6,
                    "sample_count": 55,
                    "threshold_adjustment": 0.01,
                },
            )(),
            "execution_context": {
                "analysis_mode": "history_refresh",
                "requested_as_of_date": "2026-05-20",
                "fund_codes": ["000001", "000002"],
                "data_sources": {
                    "nav_by_fund": {
                        "000001": {"has_data": True, "point_count": 1, "min_date": "2026-05-23", "max_date": "2026-05-23"},
                        "000002": {"has_data": False, "point_count": 0, "min_date": None, "max_date": None},
                    },
                    "signals_by_fund": {},
                    "rules_by_fund": {},
                    "macro_cutoff": {"cutoff_date": "2026-05-20"},
                    "oos_by_fund": {},
                },
                "data_quality_warnings": ["000002 缺少 NAV 数据"],
                "learned_params": {
                    "version_id": 42,
                    "learn_date": "2026-05-20",
                },
            },
        },
    )()
    with patch(
        "app.api.v1.advisor.execute_advisor_request",
        new=AsyncMock(return_value=([advice_buy, advice_sell], bundle)),
    ) as mock_execute:
        resp = await client.post(f"/api/v1/advisor/history/{result_id}/refresh")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "refreshed"
    assert body["id"] != result_id
    assert body["source_id"] == result_id
    assert body["updated_at"] is not None
    mock_execute.assert_awaited_once()

    original_detail_resp = await client.get(f"/api/v1/advisor/history/{result_id}")
    assert original_detail_resp.status_code == 200
    original_detail = original_detail_resp.json()
    assert original_detail["advice_date"] == "2026-05-20"

    new_detail_resp = await client.get(f"/api/v1/advisor/history/{body['id']}")
    assert new_detail_resp.status_code == 200
    detail = new_detail_resp.json()
    assert detail["advice_date"] == "2026-05-23"
    assert detail["fund_codes"] == ["000001", "000002"]
    assert detail["summary"]["buy_count"] == 1
    assert detail["advices"][0]["fund_code"] == "000002"
    assert detail["advices"][0]["trade_intent"] == "subscribe"
    assert detail["advices"][1]["trade_intent"] == "redeem"
    assert detail["user_profile"] == {"risk_level": "moderate"}
    assert detail["analysis_mode"] == "history_refresh"
    assert detail["source_result_id"] == result_id
    assert detail["learned_params_version_id"] == 42
    assert detail["execution_context"]["requested_as_of_date"] == "2026-05-20"
    assert "data_sources" in detail["execution_context"]
    assert detail["execution_context"]["data_sources"]["nav_by_fund"]["000001"]["point_count"] == 1
    assert detail["execution_context"]["data_sources"]["nav_by_fund"]["000002"]["has_data"] is False
    assert any("000002 缺少 NAV 数据" in warning for warning in detail["execution_context"]["data_quality_warnings"])
    assert detail["execution_context"]["learned_params"]["version_id"] == 42
    assert "gate_status" in detail["execution_context"]["learned_params"]
    assert detail["execution_context"]["replay"]["requested_result_id"] == result_id

    assert detail["updated_at"] is not None

    async for session in app.dependency_overrides[get_session]():
        old_row = await session.get(AdvisorResult, result_id)
        new_row = await session.get(AdvisorResult, body["id"])
        assert old_row is not None
        assert new_row is not None
        assert old_row.tracked_returns is not None
        assert old_row.tracked_at is not None
        assert new_row.tracked_returns is None
        assert new_row.tracked_at is None
        assert new_row.analysis_mode == "history_refresh"
        assert new_row.source_result_id == result_id
        assert new_row.learned_params_version_id == 42
        assert new_row.execution_context["replay"]["replayed_as_of_date"] == "2026-05-20"
        break


def test_parameter_set_id_is_stable_and_changes_with_payload():
    from app.services.advisor_parameter_governance import (
        build_default_parameter_payload,
        compute_parameter_set_id,
    )
    from app.services.advisor_profiles import build_advisor_config

    payload = build_default_parameter_payload("moderate", build_advisor_config("moderate"))
    same_payload = build_default_parameter_payload("moderate", build_advisor_config("moderate"))
    changed_payload = build_default_parameter_payload("moderate", build_advisor_config("moderate"))
    changed_payload["advisor_config"]["buy_threshold"] = 0.99

    first = compute_parameter_set_id(kind="default_config", risk_level="moderate", payload=payload)
    second = compute_parameter_set_id(kind="default_config", risk_level="moderate", payload=same_payload)
    changed = compute_parameter_set_id(kind="default_config", risk_level="moderate", payload=changed_payload)

    assert first == second
    assert first != changed



def test_parameter_registry_gate_review_activate_and_rollback(tmp_path: Path):
    from app.services import advisor_parameter_governance as governance_module
    from app.services.advisor_parameter_governance import (
        AdvisorParameterGateResult,
        AdvisorParameterRegistry,
        GATE_ACTION_ALLOW_DEFAULT,
        GATE_STATUS_APPROVED,
        RELEASE_STATUS_ACTIVE,
        RELEASE_STATUS_ROLLED_BACK,
        REVIEW_STATUS_APPROVED,
    )
    from app.services.advisor_profiles import build_advisor_config

    db_path = tmp_path / "parameter_sets.sqlite"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)

    original_db_engine = governance_module.AdvisorParameterRegistry._db_engine
    original_db_available = governance_module.AdvisorParameterRegistry._db_available
    try:
        governance_module.AdvisorParameterRegistry._db_engine = classmethod(lambda cls: engine)
        governance_module.AdvisorParameterRegistry._db_available = classmethod(lambda cls: True)

        shadow = AdvisorParameterRegistry.register_default_parameter_set(
            risk_level="moderate",
            config=build_advisor_config("moderate"),
            gate_result=AdvisorParameterGateResult(
                status="shadow_only",
                action="shadow_only",
                reason="测试门禁未通过",
                config_hash="shadow-hash",
            ),
            evaluate_gate=False,
        )
        assert shadow is not None
        with pytest.raises(ValueError):
            AdvisorParameterRegistry.activate_parameter_set(param_set_id=shadow.param_set_id)

        approved_gate = AdvisorParameterGateResult(
            status=GATE_STATUS_APPROVED,
            action=GATE_ACTION_ALLOW_DEFAULT,
            reason="测试门禁通过",
            config_hash="approved-hash",
            metrics={"coverage_ratio": 1.0},
        )
        first = AdvisorParameterRegistry.register_default_parameter_set(
            risk_level="moderate",
            config=build_advisor_config("moderate"),
            gate_result=approved_gate,
            evaluate_gate=False,
            review_status=REVIEW_STATUS_APPROVED,
        )
        assert first is not None
        active_first = AdvisorParameterRegistry.activate_parameter_set(param_set_id=first.param_set_id)
        assert active_first.release_status == RELEASE_STATUS_ACTIVE

        aggressive_config = build_advisor_config("moderate")
        aggressive_config.buy_threshold = 0.21
        second = AdvisorParameterRegistry.register_default_parameter_set(
            risk_level="moderate",
            config=aggressive_config,
            gate_result=approved_gate,
            evaluate_gate=False,
            review_status=REVIEW_STATUS_APPROVED,
        )
        assert second is not None
        active_second = AdvisorParameterRegistry.activate_parameter_set(param_set_id=second.param_set_id)
        assert active_second.release_status == RELEASE_STATUS_ACTIVE

        rolled = AdvisorParameterRegistry.rollback_parameter_set(
            risk_level="moderate",
            target_param_set_id=first.param_set_id,
            reason="测试回滚",
        )
        assert rolled.param_set_id == first.param_set_id
        assert rolled.release_status == RELEASE_STATUS_ACTIVE
        assert rolled.rollback_from_param_set_id == second.param_set_id

        with Session(engine) as session:
            rows = {row.param_set_id: row for row in session.execute(select(AdvisorParameterSet)).scalars().all()}
            assert rows[first.param_set_id].release_status == RELEASE_STATUS_ACTIVE
            assert rows[second.param_set_id].release_status == RELEASE_STATUS_ROLLED_BACK
            assert rows[second.param_set_id].rollback_reason == "测试回滚"
    finally:
        governance_module.AdvisorParameterRegistry._db_engine = original_db_engine
        governance_module.AdvisorParameterRegistry._db_available = original_db_available
        engine.dispose()




def test_parameter_gate_blocks_low_multi_objective_snapshot():
    from app.services.advisor_parameter_governance import (
        GATE_STATUS_BLOCKED,
        evaluate_parameter_gate,
    )

    snapshot = OOSValidationSnapshot(
        fund_code="000001",
        risk_level="moderate",
        updated_at="2026-05-29",
        avg_oos_ic=0.05,
        ic_degradation=0.7,
        total_oos_signals=45,
        pbo=0.25,
        cpcv_n_paths=15,
        multi_objective_score=-0.20,
        multi_objective_eliminated=True,
        multi_objective_reasons=["最大回撤超限"],
    )

    gate = evaluate_parameter_gate(
        parameter_payload={"advisor_config": {}},
        risk_level="moderate",
        fund_codes=["000001"],
        oos_snapshots={"000001": snapshot},
    )

    assert gate.status == GATE_STATUS_BLOCKED
    assert "多目标" in gate.reason
    assert gate.metrics["min_multi_objective_score"] == -0.2

def test_run_walk_forward_validation_exposes_baseline_comparison_fields():
    nav_records = [
        ((date(2024, 1, 1) + timedelta(days=i)).isoformat(), 1 + i * 0.001)
        for i in range(520)
    ]

    result = run_walk_forward_validation(
        fund_code="000001",
        nav_records=nav_records,
        fund_name="测试基金",
        fund_type="stock",
        config=AdvisorConfig(),
        n_folds=4,
        rebalance_freq=5,
    )
    payload = result.to_dict()

    assert result.multi_objective_score is not None
    assert isinstance(result.baseline_metrics, dict)
    assert {"dca", "risk_parity", "simple_momentum"}.issubset(result.baseline_metrics.keys())
    assert "baseline" in payload
    assert payload["baseline_metrics"]["dca"]["total_return"] is not None
    assert payload["summary"]["baseline_adjusted_score"] == result.baseline_adjusted_score
    assert payload["summary"]["baseline_passed"] == result.baseline_passed


def test_advisor_feedback_load_learned_supports_as_of_date(tmp_path: Path):
    base = tmp_path / "learned_params.json"
    base.write_text(
        '{"version_id":11,"version":"5.0","learn_date":"2026-05-20","sample_count":40,"confidence":0.5,"factor_ics":{},"weight_multipliers":{},"threshold_adjustment":0.01,"adjustments_log":[]}',
        encoding="utf-8",
    )
    (tmp_path / "learned_params.2026-05-18.json").write_text(
        '{"version_id":7,"version":"5.0","learn_date":"2026-05-18","sample_count":30,"confidence":0.4,"factor_ics":{},"weight_multipliers":{},"threshold_adjustment":0.02,"adjustments_log":[]}',
        encoding="utf-8",
    )
    learned = AdvisorFeedbackLearner.load_learned(
        str(base),
        as_of_date=date(2026, 5, 19),
        allow_shadow=True,
    )
    assert learned is not None
    assert learned.learn_date == "2026-05-18"
    assert learned.version_id == 7
    assert learned.engine_version == "5.0"


def test_advisor_feedback_load_learned_prefers_database_as_of_date(tmp_path: Path):
    from app.services import advisor_feedback as advisor_feedback_module

    db_path = tmp_path / "learned_versions.sqlite"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)

    original_db_engine = advisor_feedback_module.AdvisorFeedbackLearner._db_engine
    original_db_available = advisor_feedback_module.AdvisorFeedbackLearner._db_available
    try:
        advisor_feedback_module.AdvisorFeedbackLearner._db_engine = classmethod(lambda cls: engine)
        advisor_feedback_module.AdvisorFeedbackLearner._db_available = classmethod(lambda cls: True)
        learner = AdvisorFeedbackLearner(FeedbackConfig())
        first = advisor_feedback_module.LearnedWeights(
            learn_date="2026-05-18",
            sample_count=33,
            confidence=0.41,
            threshold_adjustment=0.02,
        )
        second = advisor_feedback_module.LearnedWeights(
            learn_date="2026-05-20",
            sample_count=48,
            confidence=0.66,
            threshold_adjustment=0.01,
        )
        learner._save_learned(first)
        learner._save_learned(second)

        with engine.begin() as conn:
            conn.execute(
                update(AdvisorLearnedParamsVersion).values(
                    gate_status="approved",
                    gate_action="allow_default",
                    gate_reason="测试批准",
                )
            )

        loaded = AdvisorFeedbackLearner.load_learned(as_of_date=date(2026, 5, 19))
        assert loaded is not None
        assert loaded.learn_date == "2026-05-18"
        assert loaded.version_id is not None
        assert loaded.gate_status == "approved"

        with engine.begin() as conn:
            count = conn.execute(select(AdvisorLearnedParamsVersion)).scalars().all()
            assert len(count) == 2
    finally:
        advisor_feedback_module.AdvisorFeedbackLearner._db_engine = original_db_engine
        advisor_feedback_module.AdvisorFeedbackLearner._db_available = original_db_available
        engine.dispose()


def test_advisor_feedback_shadow_params_blocked_from_default_load(tmp_path: Path):
    from app.services import advisor_feedback as advisor_feedback_module

    db_path = tmp_path / "learned_shadow.sqlite"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)

    original_db_engine = advisor_feedback_module.AdvisorFeedbackLearner._db_engine
    original_db_available = advisor_feedback_module.AdvisorFeedbackLearner._db_available
    try:
        advisor_feedback_module.AdvisorFeedbackLearner._db_engine = classmethod(lambda cls: engine)
        advisor_feedback_module.AdvisorFeedbackLearner._db_available = classmethod(lambda cls: True)
        learner = AdvisorFeedbackLearner(FeedbackConfig())
        learned = advisor_feedback_module.LearnedWeights(
            learn_date="2026-05-21",
            sample_count=80,
            confidence=0.8,
            threshold_adjustment=0.02,
        )
        learner._save_learned(learned)

        default_loaded = AdvisorFeedbackLearner.load_learned(as_of_date=date(2026, 5, 22))
        shadow_loaded = AdvisorFeedbackLearner.load_learned(
            as_of_date=date(2026, 5, 22),
            allow_shadow=True,
        )

        assert default_loaded is None
        assert shadow_loaded is not None
        assert shadow_loaded.gate_status in {"shadow_only", "blocked"}
        assert shadow_loaded.gate_action in {"shadow_only", "block_default"}
        assert shadow_loaded.config_hash is not None
    finally:
        advisor_feedback_module.AdvisorFeedbackLearner._db_engine = original_db_engine
        advisor_feedback_module.AdvisorFeedbackLearner._db_available = original_db_available
        engine.dispose()


@patch("app.services.advisor_feedback.AdvisorFeedbackLearner.load_learned")
def test_historical_backtest_disables_learned_weights(mock_load_learned):
    nav_records = [
        ((date(2024, 1, 1) + timedelta(days=i)).isoformat(), 1.0 + i * 0.001)
        for i in range(320)
    ]

    def _fake_generate(self, fund_codes, nav_data, **kwargs):
        assert self._learned_weights is None
        advice = TradingAdvice(
            fund_code=fund_codes[0],
            fund_name="测试基金",
            fund_type="stock",
            advice_date=self.as_of_date.isoformat(),
        )
        advice.action = "buy"
        advice.confidence = 0.8
        advice.composite_score = 0.6
        advice.suggested_amount = 1000.0
        return [advice]

    with patch("app.services.trading_advisor.TradingAdvisor.generate_advice", new=_fake_generate):
        result = run_advisor_backtest(
            fund_code="000001",
            nav_records=nav_records,
            fund_name="测试基金",
            fund_type="stock",
        )

    assert result.metrics.total_buy_signals > 0
    mock_load_learned.assert_not_called()


def test_advisor_backtest_uses_live_generate_advice():
    nav_records = [
        ((date(2024, 1, 1) + timedelta(days=i)).isoformat(), 1.0 + i * 0.001)
        for i in range(320)
    ]

    def _fake_generate(self, fund_codes, nav_data, **kwargs):
        advice = TradingAdvice(
            fund_code=fund_codes[0],
            fund_name="测试基金",
            fund_type="stock",
            advice_date=self.as_of_date.isoformat(),
        )
        advice.action = "buy"
        advice.confidence = 0.8
        advice.composite_score = 0.6
        advice.suggested_amount = 1000.0
        return [advice]

    with patch("app.services.trading_advisor.TradingAdvisor.generate_advice", new=_fake_generate):
        result = run_advisor_backtest(
            fund_code="000001",
            nav_records=nav_records,
            fund_name="测试基金",
            fund_type="stock",
        )

    assert result.metrics.total_buy_signals > 0
    assert all(record["action"] == "buy" for record in result.advice_records[:5])


def test_oos_snapshot_roundtrip_includes_pbo(tmp_path: Path):
    original = OOSValidationStore._path
    test_path = tmp_path / "oos_validation_snapshots.json"
    OOSValidationStore._path = staticmethod(lambda: test_path)
    try:
        snapshot = OOSValidationSnapshot(
            fund_code="000001",
            risk_level="moderate",
            updated_at="2026-05-28",
            avg_oos_ic=0.04,
            total_oos_signals=35,
            pbo=0.62,
            cpcv_n_paths=15,
            cpcv_avg_oos_sharpe=-0.12,
            cpcv_std_oos_sharpe=0.33,
            cpcv_avg_is_sharpe=0.48,
            multi_objective_score=-0.12,
            multi_objective_components={"overfit_penalty": 0.8},
            multi_objective_eliminated=True,
            multi_objective_reasons=["PBO 过高"],
            baseline_adjusted_score=-0.2,
            baseline_comparison={"risk_parity": {"score_uplift": -0.1}},
            baseline_passed=False,
            baseline_reasons=["未跑赢风险平价 baseline"],
        )
        OOSValidationStore.save(snapshot)
        loaded = OOSValidationStore.load("000001", "moderate")
        assert loaded is not None
        assert loaded.pbo == 0.62
        assert loaded.cpcv_n_paths == 15
        assert loaded.cpcv_avg_oos_sharpe == -0.12
        assert loaded.multi_objective_score == -0.12
        assert loaded.multi_objective_eliminated is True
        assert loaded.baseline_adjusted_score == -0.2
        assert loaded.baseline_passed is False
        assert loaded.to_dict()["pbo"] == 0.62
        assert loaded.to_dict()["multi_objective_components"]["overfit_penalty"] == 0.8
        assert loaded.to_dict()["baseline_comparison"]["risk_parity"]["score_uplift"] == -0.1
    finally:
        OOSValidationStore._path = original


def test_parameter_gate_blocks_snapshot_that_fails_baseline_gate():
    snapshot = OOSValidationSnapshot(
        fund_code="000001",
        risk_level="moderate",
        updated_at="2026-05-29",
        avg_oos_ic=0.08,
        ic_degradation=0.9,
        total_oos_signals=60,
        pbo=0.2,
        cpcv_n_paths=20,
        multi_objective_score=0.2,
        multi_objective_eliminated=False,
        baseline_adjusted_score=-0.2,
        baseline_passed=False,
        baseline_reasons=["未跑赢简单动量 baseline"],
    )

    result = evaluate_parameter_gate(
        parameter_payload={"test": True},
        fund_codes=["000001"],
        risk_level="moderate",
        oos_snapshots={"000001": snapshot},
    )

    assert result.status == "blocked"
    assert result.action == "block_default"
    assert result.metrics["baseline_failed_count"] == 1
    assert "baseline" in result.reason



def test_build_advisor_overfit_risk_uses_pbo_gate():
    advice = TradingAdvice(fund_code="000001", advice_date="2026-05-28")
    advice.reliability_adjustment = ReliabilityAdjustment(
        status="healthy",
        multiplier=1.0,
        confidence_multiplier=1.0,
        amount_multiplier=1.0,
        metrics={
            "oos_pbo": 0.72,
            "oos_cpcv_n_paths": 15,
            "oos_cpcv_avg_oos_sharpe": -0.2,
            "oos_avg_ic": 0.06,
            "oos_ic_degradation": 0.8,
            "oos_total_signals": 40,
        },
    )

    risk = build_advisor_overfit_risk(
        advice,
        engine_health_status="healthy",
        rolling_ic_samples=50,
    )

    assert risk.pbo == 0.72
    assert risk.cpcv_n_paths == 15
    assert risk.cpcv_avg_oos_sharpe == -0.2
    assert risk.level == "high"
    assert risk.gate_action == "hold"
    assert any("CPCV/PBO" in reason for reason in risk.reasons)


def test_advisor_walk_forward_uses_live_generate_advice():
    nav_records = [
        ((date(2023, 1, 1) + timedelta(days=i)).isoformat(), 1.0 + i * 0.001)
        for i in range(520)
    ]

    def _fake_generate(self, fund_codes, nav_data, **kwargs):
        advice = TradingAdvice(
            fund_code=fund_codes[0],
            fund_name="测试基金",
            fund_type="stock",
            advice_date=self.as_of_date.isoformat(),
        )
        advice.action = "sell" if int(self.as_of_date.strftime("%d")) % 2 == 0 else "buy"
        advice.confidence = 0.7
        advice.composite_score = -0.4 if advice.action == "sell" else 0.5
        advice.suggested_amount = 800.0
        return [advice]

    with patch("app.services.trading_advisor.TradingAdvisor.generate_advice", new=_fake_generate):
        result = run_walk_forward_validation(
            fund_code="000001",
            nav_records=nav_records,
            fund_name="测试基金",
            fund_type="stock",
            n_folds=3,
        )

    assert result.total_oos_signals > 0
    assert result.folds


@patch('app.tasks.advisor.refresh_oos_validation_cache.delay')
def test_trigger_oos_refresh_uses_nightly_config(mock_delay):
    mock_delay.return_value = type('Task', (), {'id': 'nightly-task-1'})()

    from app.api.v1.advisor import trigger_oos_cache_refresh

    result = __import__('asyncio').run(trigger_oos_cache_refresh())
    assert result['status'] == 'submitted'
    assert result['task_id'] == 'nightly-task-1'
    assert result['config']['risk_level'] == 'moderate'
    assert result['config']['dispatch_every_n'] == 10
    assert result['config']['dispatch_countdown_step'] == 30
    mock_delay.assert_called_once_with(
        risk_level='moderate',
        lookback_days=None,
        n_folds=5,
        rebalance_freq=5,
        max_funds=50,
        max_age_days=1,
        dispatch_every_n=10,
        dispatch_countdown_step=30,
    )


@pytest.mark.asyncio
async def test_oos_status_endpoint_reports_coverage(client: AsyncClient, app: FastAPI, tmp_path: Path):
    original = OOSValidationStore._path
    test_path = tmp_path / 'oos_validation_snapshots.json'
    OOSValidationStore._path = staticmethod(lambda: test_path)
    try:
        async for session in app.dependency_overrides[get_session]():
            from app.data.models.strategies import Strategy
            session.add(
                Strategy(
                    name='OOS测试策略',
                    strategy_type='momentum',
                    params={},
                    universe={'fund_codes': ['000001', '000002']},
                )
            )
            await session.commit()
            break

        OOSValidationStore.save(OOSValidationSnapshot(
            fund_code='000001',
            risk_level='moderate',
            updated_at='2026-05-26',
            avg_oos_ic=0.03,
            total_oos_signals=30,
        ))
        OOSValidationStore.save(OOSValidationSnapshot(
            fund_code='000001',
            risk_level='aggressive',
            updated_at='2026-05-27',
            avg_oos_ic=0.05,
            total_oos_signals=40,
        ))

        resp = await client.get('/api/v1/advisor/oos-status')
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body['total_active_funds'] == 2
        assert body['nightly_refresh']['risk_level'] == 'moderate'
        assert body['coverage']['moderate']['exact_count'] == 1
        assert body['coverage']['moderate']['resolved_count'] == 1
        assert body['coverage']['aggressive']['exact_count'] == 1
        assert body['coverage']['conservative']['fallback_to_moderate'] == 1
        assert body['coverage']['conservative']['missing_count'] == 1
    finally:
        OOSValidationStore._path = original


@pytest.mark.asyncio
async def test_history_list_prefers_updated_at_ordering(client: AsyncClient, app: FastAPI):
    older_id = await _insert_history_record(
        app,
        fund_codes=["000003"],
        advices=[{"fund_code": "000003", "action": "hold"}],
        updated_at=datetime(2026, 5, 21, 9, 0, tzinfo=timezone.utc),
    )
    newer_id = await _insert_history_record(
        app,
        fund_codes=["000004"],
        advices=[{"fund_code": "000004", "action": "hold"}],
        updated_at=datetime(2026, 5, 21, 11, 0, tzinfo=timezone.utc),
    )

    resp = await client.get("/api/v1/advisor/history")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items[0]["id"] == newer_id
    assert items[1]["id"] == older_id
    assert items[0]["updated_at"] is not None


@pytest.mark.asyncio
async def test_execution_records_create_list_and_enrich_history_performance(client: AsyncClient, app: FastAPI):
    result_id = await _insert_history_record(
        app,
        fund_codes=["000001", "000002"],
        advices=[
            {
                "fund_code": "000001",
                "action": "buy",
                "trade_intent": "subscribe",
                "suggested_amount": 1000,
                "suggested_pct": 0.1,
                "confidence": 0.7,
                "scores": {"composite": 0.55},
            },
            {
                "fund_code": "000002",
                "action": "sell",
                "trade_intent": "redeem",
                "suggested_amount": 500,
                "suggested_shares": 250,
                "confidence": 0.6,
                "scores": {"composite": -0.45},
            },
        ],
        tracked_returns={
            "000001": {"action": "buy", "composite_score": 0.55, "return_20d": 0.08, "hit_20d": True},
            "000002": {"action": "sell", "composite_score": -0.45, "return_20d": -0.03, "hit_20d": True},
        },
        tracked_at=datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc),
    )

    create_resp = await client.post(
        f"/api/v1/advisor/history/{result_id}/executions",
        json={
            "fund_code": "000001",
            "execution_status": "partial",
            "executed_date": "2026-05-21",
            "executed_amount": 600,
            "executed_nav": 1.02,
            "execution_channel": "test-broker",
            "deviation_reason": "先分批试买",
            "user_note": "测试执行记录",
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()["record"]
    assert created["advisor_result_id"] == result_id
    assert created["fund_code"] == "000001"
    assert created["advice_action"] == "buy"
    assert created["trade_intent"] == "subscribe"
    assert created["suggested_amount"] == 1000
    assert created["execution_status"] == "partial"
    assert created["executed_date"] == "2026-05-21"

    not_executed_resp = await client.post(
        f"/api/v1/advisor/history/{result_id}/executions",
        json={
            "fund_code": "000002",
            "execution_status": "not_executed",
            "not_executed_reason": "等待确认后再赎回",
        },
    )
    assert not_executed_resp.status_code == 200, not_executed_resp.text

    list_resp = await client.get(f"/api/v1/advisor/history/{result_id}/executions")
    assert list_resp.status_code == 200, list_resp.text
    listed = list_resp.json()
    assert len(listed["items"]) == 2
    assert listed["summary"]["actionable_advice_count"] == 2
    assert listed["summary"]["adopted_count"] == 1
    assert listed["summary"]["adoption_rate"] == 0.5
    assert listed["summary"]["status"] == "partially_adopted"
    assert listed["summary"]["by_fund"]["000001"]["amount_execution_ratio"] == 0.6
    assert listed["summary"]["by_fund"]["000002"]["latest_status"] == "not_executed"

    detail_resp = await client.get(f"/api/v1/advisor/history/{result_id}")
    assert detail_resp.status_code == 200, detail_resp.text
    detail = detail_resp.json()
    assert len(detail["execution_records"]) == 2
    assert detail["execution_summary"]["status"] == "partially_adopted"

    perf_resp = await client.get(f"/api/v1/advisor/history/{result_id}/performance")
    assert perf_resp.status_code == 200, perf_resp.text
    perf = perf_resp.json()
    assert perf["status"] == "tracked"
    assert perf["execution_summary"]["adopted_count"] == 1
    assert perf["tracked_returns"]["000001"]["execution_attribution"]["adopted"] is True
    assert perf["tracked_returns"]["000001"]["execution_attribution"]["drift_level"] == "moderate_deviation"
    assert perf["tracked_returns"]["000002"]["execution_attribution"]["latest_status"] == "not_executed"

    update_resp = await client.patch(
        f"/api/v1/advisor/executions/{created['id']}",
        json={"execution_status": "executed", "executed_amount": 1000},
    )
    assert update_resp.status_code == 200, update_resp.text
    assert update_resp.json()["record"]["execution_status"] == "executed"

    async for session in app.dependency_overrides[get_session]():
        rows = (await session.execute(select(AdvisorExecutionRecord))).scalars().all()
        assert len(rows) == 2
        assert rows[0].advisor_result_id == result_id
        break


@pytest.mark.asyncio
async def test_execution_records_csv_import_creates_valid_rows_and_reports_errors(client: AsyncClient, app: FastAPI):
    result_id = await _insert_history_record(
        app,
        fund_codes=["000001", "000002"],
        advices=[
            {
                "fund_code": "000001",
                "action": "buy",
                "trade_intent": "subscribe",
                "suggested_amount": 1000,
                "suggested_pct": 0.1,
                "confidence": 0.7,
            },
            {
                "fund_code": "000002",
                "action": "sell",
                "trade_intent": "redeem",
                "suggested_amount": 500,
                "suggested_shares": 250,
                "confidence": 0.6,
            },
        ],
    )
    csv_content = dedent(
        """\
        基金代码,执行状态,成交日期,成交金额,成交份额,成交净值,渠道,未执行原因,偏离原因,备注
        000001,已执行,2026-05-21,1000,800,1.25,天天基金,,,
        000002,未执行,,,,,,等待确认,,暂不赎回
        999999,已执行,2026-05-21,100,,,,,,
        """
    )

    resp = await client.post(
        f"/api/v1/advisor/history/{result_id}/executions/import",
        files={"file": ("executions.csv", csv_content.encode("utf-8-sig"), "text/csv")},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "partial"
    assert body["created_count"] == 2
    assert body["failed_count"] == 1
    assert body["rows"][0]["execution_status"] == "executed"
    assert body["rows"][1]["execution_status"] == "not_executed"
    assert "未找到对应基金" in body["rows"][2]["error"]
    assert body["summary"]["record_count"] == 2
    assert body["summary"]["adopted_count"] == 1

    detail_resp = await client.get(f"/api/v1/advisor/history/{result_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert len(detail["execution_records"]) == 2
    imported = {item["fund_code"]: item for item in detail["execution_records"]}
    assert imported["000001"]["source"] == "import"
    assert imported["000001"]["metadata"]["import_filename"] == "executions.csv"
    assert imported["000002"]["not_executed_reason"] == "等待确认"


@pytest.mark.asyncio
async def test_execution_records_import_rejects_unsupported_file(client: AsyncClient, app: FastAPI):
    result_id = await _insert_history_record(app)

    resp = await client.post(
        f"/api/v1/advisor/history/{result_id}/executions/import",
        files={"file": ("executions.txt", b"fund_code,execution_status\n000001,executed", "text/plain")},
    )

    assert resp.status_code == 400
    assert "仅支持 CSV" in resp.text


@pytest.mark.asyncio
async def test_execution_record_validation_blocks_missing_required_fields(client: AsyncClient, app: FastAPI):
    result_id = await _insert_history_record(
        app,
        advices=[{"fund_code": "000001", "action": "buy", "suggested_amount": 1000}],
    )

    missing_date = await client.post(
        f"/api/v1/advisor/history/{result_id}/executions",
        json={"fund_code": "000001", "execution_status": "executed"},
    )
    assert missing_date.status_code == 400
    assert "executed_date" in missing_date.text

    missing_reason = await client.post(
        f"/api/v1/advisor/history/{result_id}/executions",
        json={"fund_code": "000001", "execution_status": "not_executed"},
    )
    assert missing_reason.status_code == 400
    assert "not_executed_reason" in missing_reason.text

    missing_fund = await client.post(
        f"/api/v1/advisor/history/{result_id}/executions",
        json={
            "fund_code": "999999",
            "execution_status": "executed",
            "executed_date": "2026-05-21",
        },
    )
    assert missing_fund.status_code == 404


@pytest.mark.asyncio
async def test_save_advisor_result_creates_new_record_each_time(client: AsyncClient):
    payload = {
        "advice_date": "2026-05-23",
        "fund_codes": ["000001", "000002"],
        "total_capital": 1000,
        "risk_level": "moderate",
        "strategy_id": None,
        "strategy_name": None,
        "current_positions": {"000001": 200},
        "positions_detail": {"000001": {"amount": 200, "buy_date": "2026-05-01", "cost": 180}},
        "user_profile": {"risk_level": "moderate", "investment_horizon": "within_3_months"},
        "advices": [{"fund_code": "000001", "action": "hold"}],
        "summary": {"buy_count": 0, "sell_count": 0, "hold_count": 1, "total_buy_amount": 0, "total_sell_amount": 0, "high_confidence_signals": 0, "top_buy": None, "top_sell": None},
        "learned_params_version_id": 77,
        "parameter_set_id": "default_config_moderate_test",
        "execution_context": {
            "analysis_mode": "manual_save",
            "parameter_set": {"param_set_id": "default_config_moderate_test"},
            "learned_params": {"version_id": 77},
        },
    }

    first = await client.post("/api/v1/advisor/save", json=payload)
    second = await client.post("/api/v1/advisor/save", json=payload)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_body = first.json()
    second_body = second.json()
    assert first_body["status"] == "created"
    assert second_body["status"] == "created"
    assert first_body["id"] != second_body["id"]

    history = await client.get("/api/v1/advisor/history")
    assert history.status_code == 200
    body = history.json()
    ids = [item["id"] for item in body["items"]]
    assert first_body["id"] in ids
    assert second_body["id"] in ids

    detail = await client.get(f"/api/v1/advisor/history/{first_body['id']}")
    assert detail.status_code == 200
    saved_detail = detail.json()
    assert saved_detail["user_profile"]["investment_horizon"] == "within_3_months"
    assert saved_detail["parameter_set_id"] == "default_config_moderate_test"
    assert saved_detail["learned_params_version_id"] == 77
    assert saved_detail["execution_context"]["parameter_set"]["param_set_id"] == "default_config_moderate_test"


def test_normalize_trade_direction_supports_advice_and_signal_aliases():
    assert normalize_trade_direction("buy") == "subscribe"
    assert normalize_trade_direction("subscribe") == "subscribe"
    assert normalize_trade_direction("sell") == "redeem"
    assert normalize_trade_direction("redeem") == "redeem"
    assert normalize_trade_direction("hold") == "hold"
    assert normalize_trade_direction("unknown") == "hold"
    assert normalize_trade_direction(None) == "hold"


def test_trade_timing_before_cutoff_uses_same_trading_day():
    timing = calculate_fund_trade_timing(
        "buy",
        "stock",
        datetime(2026, 5, 26, 14, 59, 59, tzinfo=timezone(timedelta(hours=8))),
    )

    assert timing.trade_intent == "subscribe"
    assert timing.is_trading_day is True
    assert timing.is_after_cutoff is False
    assert timing.accepted_trade_date == "2026-05-26"
    assert timing.nav_date == "2026-05-26"
    assert timing.expected_confirm_date == "2026-05-27"
    assert timing.expected_available_date == "2026-05-27"


def test_trade_timing_at_cutoff_rolls_to_next_trading_day():
    timing = calculate_fund_trade_timing(
        "buy",
        "stock",
        datetime(2026, 5, 26, 15, 0, 0, tzinfo=timezone(timedelta(hours=8))),
    )

    assert timing.is_trading_day is True
    assert timing.is_after_cutoff is True
    assert timing.accepted_trade_date == "2026-05-27"
    assert timing.nav_date == "2026-05-27"
    assert timing.expected_confirm_date == "2026-05-28"
    assert any("15:00" in warning for warning in timing.warnings)


def test_trade_timing_weekend_rolls_to_next_trading_day():
    timing = calculate_fund_trade_timing(
        "sell",
        "mixed",
        datetime(2026, 5, 30, 10, 0, 0, tzinfo=timezone(timedelta(hours=8))),
    )

    assert timing.trade_intent == "redeem"
    assert timing.is_trading_day is False
    assert timing.accepted_trade_date == "2026-06-01"
    assert timing.nav_date == "2026-06-01"
    assert timing.expected_confirm_date == "2026-06-02"
    assert timing.expected_settlement_date == "2026-06-03"
    assert any("非交易日" in warning for warning in timing.warnings)


def test_qdii_redeem_timing_uses_longer_settlement_and_warning():
    timing = calculate_fund_trade_timing(
        "sell",
        "qdii",
        datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone(timedelta(hours=8))),
    )

    assert timing.trade_intent == "redeem"
    assert timing.accepted_trade_date == "2026-05-26"
    assert timing.expected_confirm_date == "2026-05-28"
    assert timing.expected_settlement_date == "2026-06-04"
    assert any("QDII" in warning for warning in timing.warnings)


def test_money_fund_redeem_timing_warns_fast_redeem_limit():
    timing = calculate_fund_trade_timing(
        "sell",
        "money",
        datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone(timedelta(hours=8))),
    )

    assert timing.trade_intent == "redeem"
    assert timing.expected_confirm_date == "2026-05-26"
    assert timing.expected_settlement_date == "2026-05-27"
    assert any("快速赎回" in warning for warning in timing.warnings)


def test_trading_advice_to_dict_exposes_trade_timing():
    advice = TradingAdvice(
        fund_code="000001",
        fund_name="测试基金",
        fund_type="stock",
        advice_date="2026-05-23",
    )
    advice.action = "buy"
    advice.trade_timing = calculate_fund_trade_timing(
        "buy",
        "stock",
        datetime(2026, 5, 26, 14, 59, 59, tzinfo=timezone(timedelta(hours=8))),
    )

    result = advice.to_dict()
    assert result["trade_timing"]["trade_intent"] == "subscribe"
    assert result["trade_timing"]["accepted_trade_date"] == "2026-05-26"
    assert result["trade_timing"]["expected_confirm_date"] == "2026-05-27"


def test_generate_advice_refreshes_trade_timing_after_rules_change_to_hold():
    def _fake_analyze(self, **kwargs):
        advice = TradingAdvice(
            fund_code=kwargs["fund_code"],
            fund_name="测试基金",
            fund_type="stock",
            advice_date="2026-05-26",
        )
        advice.action = "buy"
        advice.composite_score = 0.8
        advice.confidence = 0.8
        advice.suggested_amount = 50
        advice.estimated_gross_amount = 50
        advice.estimated_net_amount = 50
        return advice

    advisor = TradingAdvisor(
        config=AdvisorConfig(include_fee_estimate=False, min_trade_amount=0),
        total_capital=100000,
        as_of_date=date(2026, 5, 26),
    )

    with patch.object(TradingAdvisor, "_analyze_fund", new=_fake_analyze):
        advices = advisor.generate_advice(
            fund_codes=["000001"],
            nav_data={"000001": [("2026-05-26", 1.0)]},
            fund_names={"000001": "测试基金"},
            fund_types={"000001": ("stock", None)},
            fund_rules={"000001": FundTradingRules(min_purchase_amount=100)},
        )

    assert advices[0].action == "hold"
    assert advices[0].trade_timing is not None
    assert advices[0].trade_timing.trade_intent == "hold"
    assert advices[0].to_dict()["trade_timing"]["trade_intent"] == "hold"


def test_trading_advice_to_dict_exposes_trade_amount_and_shares_fields():
    advice = TradingAdvice(
        fund_code="000001",
        fund_name="测试基金",
        fund_type="stock",
        advice_date="2026-05-23",
    )
    advice.action = "sell"
    advice.suggested_amount = 1500.0
    advice.suggested_shares = 1234.5678
    advice.estimated_gross_amount = 1500.0
    advice.estimated_net_amount = 1485.0

    result = advice.to_dict()
    assert result["trade_intent"] == "redeem"
    assert result["suggested_shares"] == 1234.5678
    assert result["estimated_gross_amount"] == 1500.0
    assert result["estimated_net_amount"] == 1485.0


def test_trading_advice_to_dict_exposes_buy_net_amount_fields():
    advice = TradingAdvice(
        fund_code="000002",
        fund_name="测试基金2",
        fund_type="stock",
        advice_date="2026-05-23",
    )
    advice.action = "buy"
    advice.suggested_amount = 2000.0
    advice.estimated_gross_amount = 2000.0
    advice.estimated_net_amount = 1970.0

    result = advice.to_dict()
    assert result["trade_intent"] == "subscribe"
    assert result["suggested_shares"] is None
    assert result["estimated_gross_amount"] == 2000.0
    assert result["estimated_net_amount"] == 1970.0


def test_trading_advice_to_dict_exposes_professional_extensions():
    advice = TradingAdvice(
        fund_code="000001",
        fund_name="测试基金",
        fund_type="stock",
        advice_date="2026-05-26",
    )
    advice.action = "buy"
    advice.suggested_amount = 1000.0
    advice.position_after = 0.1
    advice.reasoning = AdviceReasoning(summary="建议分批买入", confidence_level="medium")
    advice.trade_plan = TradePlan(execution_type="batch", suggested_amount=1000.0, batch_count=3)
    advice.portfolio_impact = PortfolioImpact(before_weight=0.02, after_weight=0.10, risk_change="increase")
    advice.suitability = SuitabilityCheck(user_risk_level="moderate", fund_risk_level="R3", matched=True)
    advice.validity = AdviceValidity(
        generated_at="2026-05-26T10:00:00+08:00",
        data_as_of="2026-05-25",
        valid_until="2026-05-31",
        invalidation_rules=["超过建议有效期"],
    )
    advice.reliability_adjustment = ReliabilityAdjustment(
        status="degraded",
        multiplier=0.75,
        confidence_multiplier=0.85,
        amount_multiplier=0.7,
        reason="样本外信号偏弱",
        metrics={"rolling_ic_20d": 0.018},
    )
    advice.decision_audit = DecisionAudit(
        effective_buy_threshold=0.18,
        effective_sell_threshold=-0.18,
        threshold_state="above_buy_threshold",
        threshold_margin=0.12,
        missing_sources=1,
        signal_weights={"momentum": 0.4, "prediction": 0.2},
        signal_availability={"strategy": False, "momentum": True},
        data_quality={"nav_count": 240, "sample_sufficient": True},
        market_regime={"regime": "normal"},
        notes=["策略信号不可用"],
    )

    result = advice.to_dict()
    assert result["reasoning"]["summary"] == "建议分批买入"
    assert result["trade_plan"]["execution_type"] == "batch"
    assert result["portfolio_impact"]["risk_change"] == "increase"
    assert result["suitability"]["matched"] is True
    assert result["validity"]["valid_until"] == "2026-05-31"
    assert result["reliability_adjustment"]["status"] == "degraded"
    assert result["reliability_adjustment"]["multiplier"] == 0.75
    assert result["decision_audit"]["threshold_state"] == "above_buy_threshold"
    assert result["decision_audit"]["signal_weights"]["momentum"] == 0.4
    assert result["decision_audit"]["data_quality"]["nav_count"] == 240


def test_suitability_mismatch_reduces_buy_amount():
    advisor = TradingAdvisor(
        total_capital=100000,
        user_profile={"risk_level": "conservative"},
        config=AdvisorConfig(min_trade_amount=0),
    )
    advice = TradingAdvice(fund_code="000001", fund_type="stock")
    advice.action = "buy"
    advice.suggested_amount = 10000.0
    advice.estimated_gross_amount = 10000.0
    advice.position_after = 0.1
    advice.risk_position = RiskBudgetPosition(annualized_vol=0.25, max_drawdown_1y=-0.25)

    advisor._apply_suitability_check(advice)

    assert advice.suitability is not None
    assert advice.suitability.matched is False
    assert advice.suggested_amount == 5000.0
    assert any("风险等级" in warning for warning in advice.risk_warnings)


def test_profile_constraints_reduce_risky_buy_for_short_horizon_and_drawdown():
    advisor = TradingAdvisor(
        total_capital=100000,
        user_profile={
            "risk_level": "aggressive",
            "investment_horizon": "within_3_months",
            "liquidity_need": "high",
            "max_drawdown_tolerance": 0.08,
        },
        config=AdvisorConfig(min_trade_amount=0),
    )
    advice = TradingAdvice(fund_code="000001", fund_type="stock")
    advice.action = "buy"
    advice.suggested_amount = 10000.0
    advice.estimated_gross_amount = 10000.0
    advice.position_after = 0.1
    advice.risk_position = RiskBudgetPosition(annualized_vol=0.20, max_drawdown_1y=-0.16)

    advisor._apply_profile_constraints(advice)

    assert advice.action == "buy"
    assert advice.suggested_amount < 10000.0
    assert len(advice.profile_constraints) >= 3
    assert any(c.name == "投资期限" for c in advice.profile_constraints)
    assert any(c.name == "流动性需求" for c in advice.profile_constraints)
    assert any(c.name == "最大回撤承受力" for c in advice.profile_constraints)


def test_profile_constraints_are_included_in_reasoning_factors():
    advisor = TradingAdvisor(total_capital=100000)
    advice = TradingAdvice(fund_code="000001", fund_type="stock")
    advice.action = "hold"
    advice.profile_constraints.append(
        ProfileConstraint(
            name="投资期限",
            triggered=True,
            effect="warning",
            explanation="短期限资金应优先考虑流动性和净值波动风险",
        )
    )

    reasoning = advisor._build_reasoning(advice)

    assert any(f.name == "投资期限" for f in reasoning.factors)


def test_feedback_learner_shrinks_multiplier_threshold_and_momentum_discount():
    learner = AdvisorFeedbackLearner(
        FeedbackConfig(
            multiplier_shrinkage=0.35,
            threshold_shrinkage=0.35,
            momentum_discount_shrinkage=0.4,
            max_relative_upside=0.3,
            max_relative_downside=0.3,
        )
    )

    assert learner._shrink_multiplier(2.0) == pytest.approx(1.3)
    assert learner._shrink_multiplier(0.0) == pytest.approx(0.7)
    assert learner._shrink_threshold_adjustment(0.1) == pytest.approx(0.035)
    assert learner._shrink_threshold_adjustment(-0.1) == pytest.approx(-0.035)
    assert learner._shrink_momentum_discount(1.0) == pytest.approx(0.82)
    assert learner._shrink_momentum_discount(0.2) == pytest.approx(0.5)


def test_oos_validation_store_roundtrip(tmp_path: Path):
    original = OOSValidationStore._path
    test_path = tmp_path / 'oos_validation_snapshots.json'
    OOSValidationStore._path = staticmethod(lambda: test_path)
    try:
        moderate = OOSValidationSnapshot(
            fund_code='000001',
            risk_level='moderate',
            updated_at='2026-05-26',
            avg_oos_ic=0.03,
            ic_degradation=0.55,
            total_oos_signals=32,
        )
        aggressive = OOSValidationSnapshot(
            fund_code='000001',
            risk_level='aggressive',
            updated_at='2026-05-27',
            avg_oos_ic=0.05,
            ic_degradation=0.72,
            total_oos_signals=48,
        )
        OOSValidationStore.save(moderate)
        OOSValidationStore.save(aggressive)

        loaded_default = OOSValidationStore.load('000001')
        loaded_aggressive = OOSValidationStore.load('000001', risk_level='aggressive')
        loaded_exact_moderate = OOSValidationStore.load_exact('000001', risk_level='moderate')
        loaded_many = OOSValidationStore.load_many(['000001'], risk_level='aggressive')

        assert loaded_default is not None
        assert loaded_default.fund_code == '000001'
        assert loaded_default.risk_level == 'moderate'
        assert loaded_default.avg_oos_ic == 0.03
        assert loaded_default.ic_degradation == 0.55

        assert loaded_aggressive is not None
        assert loaded_aggressive.risk_level == 'aggressive'
        assert loaded_aggressive.avg_oos_ic == 0.05
        assert loaded_aggressive.total_oos_signals == 48

        assert loaded_exact_moderate is not None
        assert loaded_exact_moderate.risk_level == 'moderate'
        assert loaded_many['000001'].risk_level == 'aggressive'
    finally:
        OOSValidationStore._path = original



def test_oos_validation_store_uses_database_when_table_available(tmp_path: Path):
    from sqlalchemy.orm import Session

    from app.services import advisor_oos as advisor_oos_module

    db_path = tmp_path / 'oos_snapshots.sqlite'
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)

    original_db_engine = advisor_oos_module.OOSValidationStore._db_engine
    original_db_available = advisor_oos_module.OOSValidationStore._db_available
    original_path = advisor_oos_module.OOSValidationStore._path
    advisor_oos_module.OOSValidationStore._db_engine = classmethod(lambda cls: engine)
    advisor_oos_module.OOSValidationStore._db_available = classmethod(lambda cls: True)
    advisor_oos_module.OOSValidationStore._path = staticmethod(advisor_oos_module.OOSValidationStore._default_path)
    try:
        advisor_oos_module.OOSValidationStore.save(
            OOSValidationSnapshot(
                fund_code='000010',
                risk_level='moderate',
                updated_at='2026-05-26',
                avg_oos_ic=0.08,
                total_oos_signals=22,
            )
        )
        advisor_oos_module.OOSValidationStore.save(
            OOSValidationSnapshot(
                fund_code='000010',
                risk_level='moderate',
                updated_at='2026-05-27',
                avg_oos_ic=0.02,
                total_oos_signals=12,
            )
        )
        loaded = advisor_oos_module.OOSValidationStore.load('000010', risk_level='moderate')
        historical = advisor_oos_module.OOSValidationStore.load('000010', risk_level='moderate', as_of_date=date(2026, 5, 26))
        assert loaded is not None
        assert loaded.avg_oos_ic == 0.02
        assert historical is not None
        assert historical.avg_oos_ic == 0.08
        with Session(engine) as session:
            stored = session.execute(select(AdvisorOOSSnapshot)).scalars().all()
            assert len(stored) == 2
            assert {row.fund_code for row in stored} == {'000010'}
    finally:
        advisor_oos_module.OOSValidationStore._db_engine = original_db_engine
        advisor_oos_module.OOSValidationStore._db_available = original_db_available
        advisor_oos_module.OOSValidationStore._path = original_path
        engine.dispose()


def test_oos_validation_store_imports_legacy_file_when_database_empty(tmp_path: Path):
    from sqlalchemy.orm import Session

    from app.services import advisor_oos as advisor_oos_module

    db_path = tmp_path / 'oos_import.sqlite'
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)

    legacy_dir = tmp_path / 'backend' / 'app' / 'data'
    legacy_dir.mkdir(parents=True, exist_ok=True)
    legacy_path = legacy_dir / 'oos_validation_snapshots.json'
    legacy_path.write_text(
        '{\n  "000020": {\n    "moderate": {\n      "fund_code": "000020",\n      "risk_level": "moderate",\n      "updated_at": "2026-05-26",\n      "avg_oos_ic": 0.04,\n      "total_oos_signals": 18,\n      "warnings": []\n    }\n  }\n}',
        encoding='utf-8',
    )

    original_db_engine = advisor_oos_module.OOSValidationStore._db_engine
    original_db_available = advisor_oos_module.OOSValidationStore._db_available
    original_default_path = advisor_oos_module.OOSValidationStore._default_path
    original_path = advisor_oos_module.OOSValidationStore._path
    original_migration_done = advisor_oos_module.OOSValidationStore._legacy_migration_done
    advisor_oos_module.OOSValidationStore._db_engine = classmethod(lambda cls: engine)
    advisor_oos_module.OOSValidationStore._db_available = classmethod(lambda cls: True)
    advisor_oos_module.OOSValidationStore._default_path = staticmethod(lambda: legacy_path)
    advisor_oos_module.OOSValidationStore._path = staticmethod(lambda: legacy_path)
    advisor_oos_module.OOSValidationStore._legacy_migration_done = False
    try:
        imported = advisor_oos_module.OOSValidationStore.import_legacy_file_if_needed()
        assert imported == 1
        loaded = advisor_oos_module.OOSValidationStore.load('000020', risk_level='moderate')
        assert loaded is not None
        assert loaded.avg_oos_ic == 0.04
        with Session(engine) as session:
            stored = session.execute(select(AdvisorOOSSnapshot)).scalars().all()
            assert len(stored) == 1
            assert stored[0].fund_code == '000020'
    finally:
        advisor_oos_module.OOSValidationStore._db_engine = original_db_engine
        advisor_oos_module.OOSValidationStore._db_available = original_db_available
        advisor_oos_module.OOSValidationStore._default_path = original_default_path
        advisor_oos_module.OOSValidationStore._path = original_path
        advisor_oos_module.OOSValidationStore._legacy_migration_done = original_migration_done
        engine.dispose()



def test_oos_validation_store_fallback_prefers_moderate_then_latest(tmp_path: Path):
    original = OOSValidationStore._path
    test_path = tmp_path / 'oos_validation_snapshots.json'
    OOSValidationStore._path = staticmethod(lambda: test_path)
    try:
        OOSValidationStore.save(
            OOSValidationSnapshot(
                fund_code='000001',
                risk_level='moderate',
                updated_at='2026-05-26',
                avg_oos_ic=0.03,
                total_oos_signals=32,
            )
        )
        OOSValidationStore.save(
            OOSValidationSnapshot(
                fund_code='000001',
                risk_level='aggressive',
                updated_at='2026-05-27',
                avg_oos_ic=0.05,
                total_oos_signals=48,
            )
        )
        OOSValidationStore.save(
            OOSValidationSnapshot(
                fund_code='000002',
                risk_level='aggressive',
                updated_at='2026-05-28',
                avg_oos_ic=0.06,
                total_oos_signals=50,
            )
        )

        conservative_fallback = OOSValidationStore.load('000001', risk_level='conservative')
        latest_fallback = OOSValidationStore.load('000002', risk_level='conservative')
        exact_missing = OOSValidationStore.load_exact('000002', risk_level='conservative')
        batch_loaded = OOSValidationStore.load_many(['000001', '000002'], risk_level='conservative')

        assert conservative_fallback is not None
        assert conservative_fallback.risk_level == 'moderate'
        assert conservative_fallback.avg_oos_ic == 0.03
        assert getattr(conservative_fallback, 'selection_source') == 'moderate_fallback'
        assert getattr(conservative_fallback, 'requested_risk_level') == 'conservative'

        assert latest_fallback is not None
        assert latest_fallback.risk_level == 'aggressive'
        assert latest_fallback.avg_oos_ic == 0.06
        assert getattr(latest_fallback, 'selection_source') == 'latest_fallback'
        assert exact_missing is None

        assert batch_loaded['000001'].risk_level == 'moderate'
        assert getattr(batch_loaded['000001'], 'selection_source') == 'moderate_fallback'
        assert batch_loaded['000002'].risk_level == 'aggressive'
        assert getattr(batch_loaded['000002'], 'selection_source') == 'latest_fallback'
    finally:
        OOSValidationStore._path = original


def test_daily_advisor_reminder_digest_schedule_disabled_for_personal_use():
    assert 'daily-advisor-reminder-digest' not in BEAT_SCHEDULE


def test_daily_oos_validation_refresh_schedule_registered():
    entry = BEAT_SCHEDULE['daily-oos-validation-refresh']
    assert entry['task'] == 'app.tasks.advisor.refresh_oos_validation_cache'
    assert entry['options']['queue'] == 'backtest'
    assert str(entry['schedule']._orig_minute) == '40'
    assert str(entry['schedule']._orig_hour) == '21'
    assert entry['kwargs']['risk_level'] == 'moderate'
    assert entry['kwargs']['dispatch_every_n'] == 10
    assert entry['kwargs']['dispatch_countdown_step'] == 30


@patch('app.tasks.advisor.run_walk_forward_task.apply_async')
@patch('app.tasks.advisor._load_all_fund_codes')
@patch('app.services.advisor_oos.OOSValidationStore.stale_fund_codes')
def test_refresh_oos_validation_cache_dispatches_stale_funds(
    mock_stale_fund_codes,
    mock_load_all_fund_codes,
    mock_apply_async,
):
    mock_load_all_fund_codes.return_value = ['000001', '000002', '000003']
    mock_stale_fund_codes.return_value = ['000001', '000003']
    mock_apply_async.side_effect = [
        type('Task', (), {'id': 'task-1'})(),
        type('Task', (), {'id': 'task-2'})(),
    ]

    result = refresh_oos_validation_cache.run(
        risk_level='aggressive',
        lookback_days=900,
        n_folds=4,
        rebalance_freq=7,
        max_funds=3,
        max_age_days=2,
        dispatch_every_n=1,
        dispatch_countdown_step=15,
    )

    assert result['status'] == 'submitted'
    assert result['risk_level'] == 'aggressive'
    assert result['submitted_count'] == 2
    assert result['skipped_count'] == 1
    assert result['dispatch_every_n'] == 1
    assert result['dispatch_countdown_step'] == 15
    assert result['submitted'][0]['fund_code'] == '000001'
    assert result['submitted'][0]['countdown'] == 0
    assert result['submitted'][1]['fund_code'] == '000003'
    assert result['submitted'][1]['countdown'] == 15
    mock_stale_fund_codes.assert_called_once_with(
        ['000001', '000002', '000003'],
        risk_level='aggressive',
        max_age_days=2,
    )
    mock_apply_async.assert_any_call(
        kwargs={
            'fund_code': '000001',
            'lookback_days': 900,
            'n_folds': 4,
            'rebalance_freq': 7,
            'risk_level': 'aggressive',
        },
        countdown=0,
    )
    mock_apply_async.assert_any_call(
        kwargs={
            'fund_code': '000003',
            'lookback_days': 900,
            'n_folds': 4,
            'rebalance_freq': 7,
            'risk_level': 'aggressive',
        },
        countdown=15,
    )



def test_reliability_adjustment_discounts_score_confidence_and_amount():
    class Health:
        status = "unhealthy"
        rolling_ic_samples = 40
        rolling_ic_20d = -0.01
        ic_trend = "critical"
        recent_buy_hit_rate = 0.4
        recent_sell_hit_rate = 0.5
        status_reason = "IC 为负"

    oos_snapshot = OOSValidationSnapshot(
        fund_code='000001',
        avg_oos_ic=0.01,
        ic_degradation=0.25,
        total_oos_signals=36,
        avg_oos_buy_hit_rate=0.42,
        avg_oos_sell_hit_rate=0.50,
        updated_at='2026-05-26',
        multi_objective_score=-0.15,
        multi_objective_eliminated=True,
        multi_objective_reasons=['多目标稳健性不足'],
    )

    advisor = TradingAdvisor(
        total_capital=100000,
        engine_health=Health(),
        oos_snapshots={'000001': oos_snapshot},
        config=AdvisorConfig(min_trade_amount=0),
    )
    advice = TradingAdvice(fund_code="000001", fund_type="stock")
    advice.composite_score = 0.6

    advisor._apply_reliability_adjustment(advice)

    assert advice.reliability_adjustment is not None
    assert advice.reliability_adjustment.status == "unhealthy"
    assert advice.reliability_adjustment.multiplier < 0.3
    assert advice.composite_score < 0.2
    assert advice.reliability_adjustment.metrics['oos_ic_degradation'] == 0.25
    assert advice.reliability_adjustment.metrics['oos_risk_level'] == 'moderate'
    assert advice.reliability_adjustment.metrics['oos_multi_objective_score'] == -0.15
    assert any("防过拟合可靠性折扣" in warning for warning in advice.risk_warnings)

    advice.action = "buy"
    advice.confidence = 0.8
    advice.risk_position = RiskBudgetPosition(suggested_amount=10000, suggested_position_pct=0.1)
    advisor._determine_action(advice, nav_values=[1.0] * 120, profile={}, fee_info=None)

    assert advice.confidence < 0.8
    assert advice.suggested_amount < 5000


def test_decision_audit_records_thresholds_weights_and_data_quality():
    advisor = TradingAdvisor(total_capital=100000)
    advice = TradingAdvice(fund_code="000001", fund_type="stock")
    advice.action = "buy"
    advice.composite_score = 0.31
    advice._effective_buy_threshold = 0.18
    advice._effective_sell_threshold = -0.18
    advice._missing_sources = 2
    advice._signal_weights = {"technical": 0.2, "momentum": 0.4, "prediction": 0.4}
    advice._signal_availability = {"strategy": False, "momentum": True, "prediction": True}
    advice._market_regime_audit = {"regime": "bull", "signal_weight_multiplier": 1.1}

    audit = advisor._build_decision_audit(
        advice,
        [("2026-01-01", 1.0), ("2026-05-26", 1.2)],
    )

    assert audit.threshold_state == "above_buy_threshold"
    assert audit.threshold_margin == pytest.approx(0.13)
    assert audit.missing_sources == 2
    assert audit.signal_weights["momentum"] == 0.4
    assert audit.signal_availability["strategy"] is False
    assert audit.data_quality["nav_count"] == 2
    assert audit.data_quality["sample_sufficient"] is False
    assert audit.market_regime["regime"] == "bull"
    assert any("信号源不可用" in note for note in audit.notes)


def test_position_detail_payload_normalizes_new_and_legacy_fields():
    legacy = PositionDetailPayload(amount=1200, cost=10000, buy_date="2026-05-01")
    assert legacy.shares == 1200
    assert legacy.cost_basis == 10000
    legacy_dict = legacy.to_legacy_dict()
    assert legacy_dict["amount"] == 1200
    assert legacy_dict["cost"] == 10000

    explicit = PositionDetailPayload(
        market_value=15000,
        shares=888,
        cost_basis=12000,
        amount=999,
        cost=13000,
    )
    assert explicit.shares == 888
    assert explicit.cost_basis == 12000
    explicit_dict = explicit.to_legacy_dict()
    assert explicit_dict["market_value"] == 15000
    assert explicit_dict["shares"] == 888
    assert explicit_dict["amount"] == 888


def test_sell_advice_uses_explicit_market_value_and_shares():
    advisor = TradingAdvisor(
        config=AdvisorConfig(
            sell_threshold=-0.1,
            include_fee_estimate=False,
            min_trade_amount=0,
            max_daily_trade_pct=1.0,
        ),
        total_capital=100000,
        current_positions={"000001": 1000},
        positions_detail={
            "000001": {
                "market_value": 20000,
                "shares": 5000,
                "cost_basis": 25000,
                "buy_date": "2026-01-01",
            }
        },
        as_of_date=date(2026, 5, 23),
    )
    advice = TradingAdvice(fund_code="000001", fund_type="stock")
    advice.composite_score = -0.8

    advisor._determine_action(advice, nav_values=[1.0] * 120, profile={}, fee_info=None)

    assert advice.action == "sell"
    assert advice.suggested_amount == 20000
    assert advice.suggested_shares == 5000
    assert advice.estimated_gross_amount == 20000
    assert advice.estimated_net_amount == 20000


def test_purchase_rules_block_amount_below_minimum():
    advice = TradingAdvice(fund_code="000001")
    advice.action = "buy"
    advice.suggested_amount = 50
    advice.estimated_gross_amount = 50
    advice.estimated_net_amount = 50

    rules = FundTradingRules(min_purchase_amount=100)
    apply_fund_trading_rules(advice, rules)

    assert advice.action == "hold"
    assert advice.suggested_amount == 0
    assert any("最低申购金额" in reason for reason in advice.reasons)


def test_purchase_rules_cap_single_purchase_limit():
    advice = TradingAdvice(fund_code="000001")
    advice.action = "buy"
    advice.suggested_amount = 10000
    advice.estimated_gross_amount = 10000
    advice.estimated_net_amount = 10000

    rules = FundTradingRules(purchase_limit=3000)
    apply_fund_trading_rules(advice, rules)

    assert advice.action == "buy"
    assert advice.suggested_amount == 3000
    assert advice.estimated_gross_amount == 3000
    assert any("单笔申购限额" in warning for warning in advice.risk_warnings)


def test_redeem_rules_block_when_redeem_suspended():
    advice = TradingAdvice(fund_code="000001")
    advice.action = "sell"
    advice.suggested_amount = 1000
    advice.suggested_shares = 1000
    advice.estimated_gross_amount = 1000
    advice.estimated_net_amount = 1000

    rules = FundTradingRules(is_redeemable=False)
    apply_fund_trading_rules(advice, rules)

    assert advice.action == "hold"
    assert advice.suggested_amount == 0
    assert advice.suggested_shares is None
    assert any("暂停赎回" in reason for reason in advice.reasons)


def test_redeem_rules_block_below_min_redeem_shares():
    advice = TradingAdvice(fund_code="000001")
    advice.action = "sell"
    advice.suggested_amount = 500
    advice.suggested_shares = 50
    advice.estimated_gross_amount = 500
    advice.estimated_net_amount = 500

    rules = FundTradingRules(min_redeem_shares=100)
    apply_fund_trading_rules(advice, rules)

    assert advice.action == "hold"
    assert any("最低赎回份额" in reason for reason in advice.reasons)


def test_redeem_rules_warn_on_min_holding_shares():
    advice = TradingAdvice(fund_code="000001")
    advice.action = "sell"
    advice.suggested_amount = 900
    advice.suggested_shares = 900
    advice._current_shares = 1000
    advice.estimated_gross_amount = 900
    advice.estimated_net_amount = 900

    rules = FundTradingRules(min_holding_shares=200)
    apply_fund_trading_rules(advice, rules)

    assert advice.action == "sell"
    assert any("最低保留份额" in warning for warning in advice.risk_warnings)


@pytest.mark.asyncio
async def test_history_as_of_date_loaders_exclude_future_data(app: FastAPI):
    async for session in app.dependency_overrides[get_session]():
        ts = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
        session.add_all([
            Fund(code="000001", name="基金A", fund_type="stock", status="active", is_purchasable=True, updated_at=ts),
            Fund(code="000002", name="基金B", fund_type="stock", status="active", is_purchasable=True, updated_at=ts),
            Fund(code="000003", name="基金C", fund_type="stock", status="active", is_purchasable=True, updated_at=ts),
        ])
        session.add_all([
            FundNav(fund_code="000001", trade_date=date(2026, 5, 18), unit_nav=Decimal("1.00"), adj_nav=Decimal("1.00"), created_at=ts),
            FundNav(fund_code="000001", trade_date=date(2026, 5, 19), unit_nav=Decimal("1.10"), adj_nav=Decimal("1.10"), created_at=ts),
            FundNav(fund_code="000001", trade_date=date(2026, 5, 20), unit_nav=Decimal("1.20"), adj_nav=Decimal("1.20"), created_at=ts),
            FundNav(fund_code="000001", trade_date=date(2026, 5, 21), unit_nav=Decimal("9.99"), adj_nav=Decimal("9.99"), created_at=ts),
            FundNav(fund_code="000002", trade_date=date(2026, 5, 18), unit_nav=Decimal("1.00"), adj_nav=Decimal("1.00"), created_at=ts),
            FundNav(fund_code="000002", trade_date=date(2026, 5, 19), unit_nav=Decimal("1.01"), adj_nav=Decimal("1.01"), created_at=ts),
            FundNav(fund_code="000002", trade_date=date(2026, 5, 20), unit_nav=Decimal("1.02"), adj_nav=Decimal("1.02"), created_at=ts),
            FundNav(fund_code="000003", trade_date=date(2026, 5, 18), unit_nav=Decimal("1.00"), adj_nav=Decimal("1.00"), created_at=ts),
            FundNav(fund_code="000003", trade_date=date(2026, 5, 19), unit_nav=Decimal("1.01"), adj_nav=Decimal("1.01"), created_at=ts),
            FundNav(fund_code="000003", trade_date=date(2026, 5, 20), unit_nav=Decimal("1.03"), adj_nav=Decimal("1.03"), created_at=ts),
        ])
        session.add_all([
            Signal(strategy_id=1, strategy_name="策略A", fund_code="000001", signal_date=date(2026, 5, 19), direction="subscribe", strength=Decimal("0.60"), reason="old"),
            Signal(strategy_id=1, strategy_name="策略A", fund_code="000001", signal_date=date(2026, 5, 21), direction="redeem", strength=Decimal("0.90"), reason="future"),
        ])
        session.add_all([
            BenchmarkNav(index_code="000300", trade_date=date(2026, 5, 18), daily_return=Decimal("0.010000"), close=Decimal("100.0"), created_at=ts),
            BenchmarkNav(index_code="000300", trade_date=date(2026, 5, 19), daily_return=Decimal("0.020000"), close=Decimal("101.0"), created_at=ts),
            BenchmarkNav(index_code="000300", trade_date=date(2026, 5, 21), daily_return=Decimal("0.990000"), close=Decimal("199.0"), created_at=ts),
            IndexValuation(index_code="000300", trade_date=date(2026, 5, 19), pe_percentile=Decimal("0.20"), pb_percentile=Decimal("0.30"), created_at=ts),
            IndexValuation(index_code="000300", trade_date=date(2026, 5, 21), pe_percentile=Decimal("0.95"), pb_percentile=Decimal("0.95"), created_at=ts),
        ])
        session.add_all([
            FundMetaHistory(fund_code="000001", effective_date=date(2026, 5, 18), fund_size=Decimal("100000000.00"), management_fee=Decimal("0.0150"), status="active", is_purchasable=True),
            FundMetaHistory(fund_code="000002", effective_date=date(2026, 5, 18), fund_size=Decimal("110000000.00"), management_fee=Decimal("0.0150"), status="active", is_purchasable=True),
            FundMetaHistory(fund_code="000003", effective_date=date(2026, 5, 18), fund_size=Decimal("120000000.00"), management_fee=Decimal("0.0150"), status="active", is_purchasable=True),
            FundMetaHistory(fund_code="000003", effective_date=date(2026, 5, 21), fund_size=Decimal("900000000.00"), management_fee=Decimal("0.0300"), status="suspended", is_purchasable=False),
        ])
        await session.commit()

        cutoff = date(2026, 5, 20)
        nav_data = await load_nav_data_for_advisor(["000001"], session, lookback_days=10, as_of_date=cutoff)
        strategy_signals = await load_strategy_signals_for_advisor(["000001"], session, strategy_id=1, as_of_date=cutoff)
        benchmark_returns, valuation_data = await load_macro_data(session, as_of_date=cutoff)
        scoring_data = await load_fund_data_for_scoring(session, fund_type="stock", min_history_days=3, as_of_date=cutoff)
        trading_rules = await load_fund_trading_rules(["000003"], session, as_of_date=cutoff)

        assert [row[0] for row in nav_data["000001"]] == ["2026-05-18", "2026-05-19", "2026-05-20"]
        assert nav_data["000001"][-1][1] == 1.2
        assert strategy_signals["000001"]["direction"] == "subscribe"
        assert strategy_signals["000001"]["signal_date"] == "2026-05-19"
        assert benchmark_returns["000300"] == [0.01, 0.02]
        assert valuation_data["000300"]["pe_percentile"] == 0.2
        assert sorted(item["fund_code"] for item in scoring_data) == ["000001", "000002", "000003"]
        score_map = {item["fund_code"]: item for item in scoring_data}
        assert score_map["000003"]["fund_size"] == 1.2
        assert score_map["000003"]["management_fee"] == 0.015
        assert trading_rules["000003"].status == "active"
        assert trading_rules["000003"].is_purchasable is True
        break


@pytest.mark.asyncio
async def test_build_execution_bundle_history_refresh_uses_as_of_date_context(app: FastAPI, tmp_path: Path):
    async for session in app.dependency_overrides[get_session]():
        ts = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
        session.add_all([
            Fund(code="000001", name="基金A", fund_type="stock", status="active", source="eastmoney", is_purchasable=True, updated_at=ts),
            Fund(code="000002", name="基金B", fund_type="stock", status="active", source="akshare", is_purchasable=True, updated_at=ts),
            Fund(code="000003", name="基金C", fund_type="stock", status="active", source="eastmoney", is_purchasable=True, updated_at=ts),
        ])
        session.add_all([
            FundNav(fund_code="000001", trade_date=date(2026, 5, 18), unit_nav=Decimal("1.00"), adj_nav=Decimal("1.00"), source="eastmoney", created_at=ts),
            FundNav(fund_code="000001", trade_date=date(2026, 5, 19), unit_nav=Decimal("1.05"), adj_nav=None, source="akshare", created_at=ts),
            FundNav(fund_code="000001", trade_date=date(2026, 5, 20), unit_nav=Decimal("1.10"), adj_nav=Decimal("1.10"), source="eastmoney", created_at=ts),
            FundNav(fund_code="000001", trade_date=date(2026, 5, 21), unit_nav=Decimal("8.88"), adj_nav=Decimal("8.88"), source="eastmoney", created_at=ts),
            FundNav(fund_code="000002", trade_date=date(2026, 5, 18), unit_nav=Decimal("1.00"), adj_nav=Decimal("1.00"), created_at=ts),
            FundNav(fund_code="000002", trade_date=date(2026, 5, 19), unit_nav=Decimal("1.02"), adj_nav=Decimal("1.02"), created_at=ts),
            FundNav(fund_code="000002", trade_date=date(2026, 5, 20), unit_nav=Decimal("1.03"), adj_nav=Decimal("1.03"), created_at=ts),
            FundNav(fund_code="000003", trade_date=date(2026, 5, 18), unit_nav=Decimal("1.00"), adj_nav=Decimal("1.00"), created_at=ts),
            FundNav(fund_code="000003", trade_date=date(2026, 5, 19), unit_nav=Decimal("1.01"), adj_nav=Decimal("1.01"), created_at=ts),
            FundNav(fund_code="000003", trade_date=date(2026, 5, 20), unit_nav=Decimal("1.04"), adj_nav=Decimal("1.04"), created_at=ts),
        ])
        session.add_all([
            Signal(strategy_id=1, strategy_name="策略A", fund_code="000001", signal_date=date(2026, 5, 19), direction="subscribe", strength=Decimal("0.51"), reason="old"),
            Signal(strategy_id=1, strategy_name="策略A", fund_code="000001", signal_date=date(2026, 5, 21), direction="redeem", strength=Decimal("0.99"), reason="future"),
        ])
        session.add_all([
            AdvisorResult(
                advice_date=date(2026, 5, 19),
                fund_codes=["000001"],
                total_capital=Decimal("1000.00"),
                risk_level="moderate",
                advices=[{"fund_code": "000001", "action": "buy", "advice_date": "2026-05-19"}],
                summary={},
                current_positions={},
                positions_detail={},
                user_profile={"risk_level": "moderate"},
                created_at=ts,
                updated_at=ts,
            ),
            AdvisorResult(
                advice_date=date(2026, 5, 21),
                fund_codes=["000001"],
                total_capital=Decimal("1000.00"),
                risk_level="moderate",
                advices=[{"fund_code": "000001", "action": "sell", "advice_date": "2026-05-21"}],
                summary={},
                current_positions={},
                positions_detail={},
                user_profile={"risk_level": "moderate"},
                tracked_returns={"000001": {"action": "buy", "composite_score": 0.5, "return_20d": 0.1, "hit_20d": True}},
                created_at=ts,
                updated_at=ts,
            ),
        ])
        session.add_all([
            BenchmarkNav(index_code="000300", trade_date=date(2026, 5, 18), daily_return=Decimal("0.010000"), close=Decimal("100.0"), created_at=ts),
            BenchmarkNav(index_code="000300", trade_date=date(2026, 5, 19), daily_return=Decimal("0.015000"), close=Decimal("101.0"), created_at=ts),
            BenchmarkNav(index_code="000300", trade_date=date(2026, 5, 20), daily_return=Decimal("0.020000"), close=Decimal("102.0"), created_at=ts),
            BenchmarkNav(index_code="000300", trade_date=date(2026, 5, 21), daily_return=Decimal("0.500000"), close=Decimal("150.0"), created_at=ts),
            IndexValuation(index_code="000300", trade_date=date(2026, 5, 20), pe_percentile=Decimal("0.25"), pb_percentile=Decimal("0.35"), created_at=ts),
            IndexValuation(index_code="000300", trade_date=date(2026, 5, 21), pe_percentile=Decimal("0.99"), pb_percentile=Decimal("0.99"), created_at=ts),
        ])
        session.add_all([
            FundMetaHistory(fund_code="000001", effective_date=date(2026, 5, 18), fund_size=Decimal("100000000.00"), management_fee=Decimal("0.0150"), status="active", is_purchasable=True),
            FundMetaHistory(fund_code="000002", effective_date=date(2026, 5, 18), fund_size=Decimal("110000000.00"), management_fee=Decimal("0.0150"), status="active", is_purchasable=True),
            FundMetaHistory(fund_code="000003", effective_date=date(2026, 5, 18), fund_size=Decimal("120000000.00"), management_fee=Decimal("0.0150"), status="active", is_purchasable=True),
            FundMetaHistory(fund_code="000001", effective_date=date(2026, 5, 21), fund_size=Decimal("999000000.00"), management_fee=Decimal("0.0300"), status="suspended", is_purchasable=False),
        ])
        moderate_snapshot = OOSValidationSnapshot(
            fund_code="000001",
            risk_level="moderate",
            updated_at="2026-05-19",
            requested_days=120,
            actual_trading_days=90,
            avg_oos_ic=0.03,
            avg_is_ic=0.05,
            ic_degradation=0.6,
            avg_oos_buy_hit_rate=0.55,
            avg_oos_sell_hit_rate=0.45,
            total_oos_signals=30,
            total_oos_buy=18,
            total_oos_sell=12,
            warnings=[],
        )
        original_path = OOSValidationStore._path
        original_snapshot_base_dir = snapshot_module._DEFAULT_BASE_DIR
        snapshot_path = tmp_path / "history_refresh_oos.json"
        snapshot_base_dir = tmp_path / "snapshots"
        snapshot_module._DEFAULT_BASE_DIR = snapshot_base_dir
        archive = SnapshotArchive(base_dir=snapshot_base_dir)
        archive.save_raw(
            provider="eastmoney",
            fund_code="000001",
            endpoint="nav_history",
            ext="json",
            data=b'{"items": ["historical"]}',
            snapshot_date=date(2026, 5, 19),
            captured_at=datetime(2026, 5, 19, 9, 30, tzinfo=timezone.utc),
        )
        archive.save_raw(
            provider="eastmoney",
            fund_code="000001",
            endpoint="nav_history",
            ext="json",
            data=b'{"items": ["future"]}',
            snapshot_date=date(2026, 5, 21),
            captured_at=datetime(2026, 5, 21, 9, 30, tzinfo=timezone.utc),
        )
        archive.save_raw(
            provider="eastmoney",
            fund_code="000001",
            endpoint="fund_meta",
            ext="html",
            data=b"<html>historical meta</html>",
            snapshot_date=date(2026, 5, 18),
            captured_at=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
        )
        archive.save_raw(
            provider="eastmoney",
            fund_code="000001",
            endpoint="fund_meta",
            ext="html",
            data=b"<html>future meta</html>",
            snapshot_date=date(2026, 5, 21),
            captured_at=datetime(2026, 5, 21, 9, 0, tzinfo=timezone.utc),
        )
        historical_nav_version = archive.latest_version(
            provider="eastmoney",
            fund_code="000001",
            endpoint="nav_history",
            as_of=date(2026, 5, 20),
        )
        historical_meta_version = archive.latest_version(
            provider="eastmoney",
            fund_code="000001",
            endpoint="fund_meta",
            as_of=date(2026, 5, 20),
        )
        assert historical_nav_version is not None
        assert historical_meta_version is not None
        OOSValidationStore._path = staticmethod(lambda: snapshot_path)
        OOSValidationStore.save(moderate_snapshot)
        await session.commit()

        request = AdvisorExecutionRequest(
            fund_codes=["000001"],
            total_capital=1000.0,
            risk_level="moderate",
            user_profile={"risk_level": "moderate"},
            strategy_id=1,
            as_of_date=date(2026, 5, 20),
            mode="history_refresh",
            enable_reliability_layers=True,
            enable_learned_weights=False,
        )
        bundle = await build_execution_bundle(request, session)

        assert bundle.nav_data["000001"][-1][0] == "2026-05-20"
        assert bundle.strategy_signals["000001"]["signal_date"] == "2026-05-19"
        assert bundle.last_advices["000001"]["action"] == "buy"
        assert bundle.execution_context["requested_as_of_date"] == "2026-05-20"
        assert bundle.execution_context["nav_coverage"]["max_points"] == 3
        assert bundle.execution_context["engine_health"]["rolling_ic_samples"] == 0
        assert bundle.oos_snapshots["000001"].updated_at == "2026-05-19"
        assert bundle.oos_snapshots["000001"].avg_oos_ic == 0.03
        assert bundle.execution_context["macro_score"] == bundle.macro_score
        assert bundle.execution_context["parameter_set"]["resolution_source"] == "built_in_default"
        nav_audit = bundle.execution_context["data_sources"]["nav_by_fund"]["000001"]
        assert nav_audit["source_consistency"]["source_count"] == 2
        assert nav_audit["source_consistency"]["source_switch_count"] == 2
        assert nav_audit["adjustment_consistency"]["fallback_to_unit_count"] == 1
        assert nav_audit["snapshot_lookup_as_of"] == "2026-05-20"
        assert nav_audit["snapshot_provider"] == "eastmoney"
        assert nav_audit["snapshot_version_id"] == historical_nav_version.version_id
        assert nav_audit["snapshot_captured_at"] == historical_nav_version.captured_at.isoformat()
        assert nav_audit["snapshot_sha256"] == historical_nav_version.sha256
        assert nav_audit["snapshot_versions"]["eastmoney"]["version_id"] == historical_nav_version.version_id
        rules_audit = bundle.execution_context["data_sources"]["rules_by_fund"]["000001"]
        assert rules_audit["source"] == "eastmoney"
        assert rules_audit["snapshot_lookup_as_of"] == "2026-05-20"
        assert rules_audit["snapshot_version_id"] == historical_meta_version.version_id
        assert rules_audit["snapshot_captured_at"] == historical_meta_version.captured_at.isoformat()
        assert bundle.nav_quality_diagnostics["000001"]["adjustment_consistency"]["adjusted_count"] == 2

        OOSValidationStore._path = original_path
        snapshot_module._DEFAULT_BASE_DIR = original_snapshot_base_dir
        if snapshot_path.exists():
            snapshot_path.unlink()
        break


@pytest.mark.asyncio
async def test_build_execution_bundle_uses_active_parameter_set(app: FastAPI, tmp_path: Path):
    from app.services import advisor_parameter_governance as governance_module
    from app.services.advisor_parameter_governance import (
        AdvisorParameterGateResult,
        AdvisorParameterRegistry,
        GATE_ACTION_ALLOW_DEFAULT,
        GATE_STATUS_APPROVED,
        REVIEW_STATUS_APPROVED,
    )
    from app.services.advisor_profiles import build_advisor_config

    db_path = tmp_path / "execution_parameter_sets.sqlite"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(sync_engine)

    active_config = build_advisor_config("moderate")
    active_config.buy_threshold = 0.33

    original_db_engine = governance_module.AdvisorParameterRegistry._db_engine
    original_db_available = governance_module.AdvisorParameterRegistry._db_available
    governance_module.AdvisorParameterRegistry._db_engine = classmethod(lambda cls: sync_engine)
    governance_module.AdvisorParameterRegistry._db_available = classmethod(lambda cls: True)
    try:
        registered = AdvisorParameterRegistry.register_default_parameter_set(
            risk_level="moderate",
            config=active_config,
            gate_result=AdvisorParameterGateResult(
                status=GATE_STATUS_APPROVED,
                action=GATE_ACTION_ALLOW_DEFAULT,
                reason="测试门禁通过",
                config_hash="active-hash",
            ),
            evaluate_gate=False,
            review_status=REVIEW_STATUS_APPROVED,
        )
        assert registered is not None
        AdvisorParameterRegistry.activate_parameter_set(param_set_id=registered.param_set_id)

        async for session in app.dependency_overrides[get_session]():
            request = AdvisorExecutionRequest(
                fund_codes=[],
                total_capital=1000.0,
                risk_level="moderate",
                enable_reliability_layers=False,
                enable_learned_weights=False,
            )
            bundle = await build_execution_bundle(request, session)

            assert bundle.config.buy_threshold == pytest.approx(0.33)
            assert bundle.parameter_set is not None
            assert bundle.parameter_set.param_set_id == registered.param_set_id
            assert bundle.execution_context["parameter_set"]["resolution_source"] == "active_registry"
            assert bundle.execution_context["parameter_set"]["param_set_id"] == registered.param_set_id
            break
    finally:
        governance_module.AdvisorParameterRegistry._db_engine = original_db_engine
        governance_module.AdvisorParameterRegistry._db_available = original_db_available
        sync_engine.dispose()



def test_oos_validation_store_respects_as_of_date(tmp_path: Path):
    original = OOSValidationStore._path
    test_path = tmp_path / 'oos_validation_snapshots.json'
    OOSValidationStore._path = staticmethod(lambda: test_path)
    try:
        OOSValidationStore.save(
            OOSValidationSnapshot(
                fund_code='000001',
                risk_level='moderate',
                updated_at='2026-05-19',
                avg_oos_ic=0.03,
                total_oos_signals=20,
            )
        )
        OOSValidationStore.save(
            OOSValidationSnapshot(
                fund_code='000001',
                risk_level='moderate',
                updated_at='2026-05-21',
                avg_oos_ic=0.99,
                total_oos_signals=99,
            )
        )

        historical = OOSValidationStore.load('000001', as_of_date=date(2026, 5, 20))
        latest = OOSValidationStore.load('000001')
        exact_historical = OOSValidationStore.load_exact('000001', as_of_date=date(2026, 5, 20))
        batch_historical = OOSValidationStore.load_many(['000001'], as_of_date=date(2026, 5, 20))

        assert historical is not None
        assert historical.updated_at == '2026-05-19'
        assert historical.avg_oos_ic == 0.03
        assert exact_historical is not None
        assert exact_historical.updated_at == '2026-05-19'
        assert batch_historical['000001'].updated_at == '2026-05-19'
        assert latest is not None
        assert latest.updated_at == '2026-05-21'
    finally:
        OOSValidationStore._path = original
