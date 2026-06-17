import { useMemo } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import {
  Spin,
  Alert,
  Button,
  Empty,
  Space,
} from 'antd';
import { ArrowLeftOutlined, SearchOutlined, FundOutlined, LineChartOutlined } from '@ant-design/icons';
import { useFundDetail, useFundNav, useFundNavQuality, useFundHoldings } from '@/api/funds';
import { DetailSection } from '@/components/DetailSection';
import { FundBasicInfoCard } from '@/components/FundBasicInfoCard';
import { FundDataQualityGate } from '@/components/FundDataQualityGate';
import { FundDataQualitySnapshot } from '@/components/FundDataQualitySnapshot';
import { FundMetricsSummary } from '@/components/FundMetricsSummary';
import { FundHoldingsDistribution } from '@/components/FundHoldingsDistribution';
import { FundNavChartCard } from '@/components/FundNavChartCard';
import { PageHero } from '@/components/PageHero';
import { PersonalResearchScoreCard } from '@/components/PersonalResearchScoreCard';
import { StatCard } from '@/components/StatCard';
import { getDefaultOneYearRange } from '@/utils/dateRange';
import { buildFundNavChartOption } from '@/utils/fundNavChart';
import { buildFeeData, buildPerformanceMetrics } from '@/utils/fundMetrics';
import { fundTypeLabel } from '@/utils/fundType';
import { buildPersonalResearchScore } from '@/utils/personalResearchScore';

export function FundDetailPage() {
  const { code } = useParams<{ code: string }>();
  const navigate = useNavigate();

  const {
    data: fund,
    isLoading: fundLoading,
    isError: fundError,
    error: fundErrorObj,
  } = useFundDetail(code || '');

  const defaultDateRange = useMemo(() => getDefaultOneYearRange(), []);

  const {
    data: navData,
    isLoading: navLoading,
    isError: navError,
  } = useFundNav(code || '', {
    start_date: defaultDateRange.startDate,
    end_date: defaultDateRange.endDate,
  });

  const {
    data: navQuality,
    isLoading: navQualityLoading,
  } = useFundNavQuality(code || '', {
    start_date: defaultDateRange.startDate,
    end_date: defaultDateRange.endDate,
  });

  const {
    data: holdingsData,
    isLoading: holdingsLoading,
  } = useFundHoldings(code || '');

  const navChartOption = useMemo(() => buildFundNavChartOption(navData), [navData]);
  const performanceMetrics = useMemo(() => buildPerformanceMetrics(navData), [navData]);
  const personalResearchScore = useMemo(() => {
    if (!fund) return null;
    return buildPersonalResearchScore(fund, navData, navQuality, holdingsData);
  }, [fund, navData, navQuality, holdingsData]);

  const latestNavDate = navQuality?.last_nav_date || navData?.records?.[navData.records.length - 1]?.trade_date || null;
  const feeData = useMemo(() => buildFeeData(fund), [fund]);

  if (fundLoading) {
    return (
      <div className="page-shell">
        <div style={{ textAlign: 'center', padding: 100 }}>
          <Spin size="large" />
        </div>
      </div>
    );
  }

  if (fundError) {
    return (
      <div className="page-shell">
        <Alert
          type="error"
          message="加载失败"
          description={
            fundErrorObj instanceof Error
              ? fundErrorObj.message
              : `获取基金 ${code} 信息时发生错误。`
          }
          showIcon
          action={
            <Button size="small" icon={<ArrowLeftOutlined />} onClick={() => navigate('/funds')}>
              返回列表
            </Button>
          }
        />
      </div>
    );
  }

  if (!fund) {
    return (
      <div className="page-shell">
        <Empty
          description={`基金 ${code} 不存在`}
          image={Empty.PRESENTED_IMAGE_SIMPLE}
        >
          <Space wrap>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/funds')}>
              返回列表
            </Button>
            <Link to="/discovery">
              <Button icon={<SearchOutlined />}>去基金发现</Button>
            </Link>
          </Space>
        </Empty>
      </div>
    );
  }

  return (
    <div className="detail-shell">
      <PageHero
        variant="detail"
        eyebrow={<><FundOutlined /> Fund Detail</>}
        title={
          <>
            {fund.name}
            <span style={{ marginLeft: 10, fontSize: 18, opacity: 0.85 }}>({fund.code})</span>
          </>
        }
        meta={
          <>
            <span className="detail-pill">{fundTypeLabel(fund.fund_type || '')}</span>
            {fund.inception_date && <span className="detail-pill">成立 {fund.inception_date}</span>}
            {fund.status && <span className="detail-pill">状态 {fund.status}</span>}
            {latestNavDate && <span className="detail-pill">最新 NAV {latestNavDate}</span>}
          </>
        }
        description="这里整合基金基本信息、数据质量、净值曲线、持仓分布和个人研究评分。你可以先看质量，再看收益与风险，最后结合持仓结构判断是否值得继续研究。"
        actions={
          <>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/funds')}>
              返回列表
            </Button>
            <Link to="/funds">
              <Button icon={<SearchOutlined />}>继续检索</Button>
            </Link>
            <Link to="/backtests">
              <Button icon={<LineChartOutlined />}>查看回测</Button>
            </Link>
          </>
        }
        stats={
          <>
            <StatCard label="净值状态" value={latestNavDate || '暂无'} note="用于判断数据是否适合研究、筛选和回测" />
            <StatCard label="数据质量" value={navQuality?.status || '未知'} note="结合缺口、跳变和复权覆盖率解读" />
            <StatCard label="研究评分" value={personalResearchScore ? Math.round(personalResearchScore.total) : 'N/A'} note="仅用于个人候选池排序，不构成建议" />
            <StatCard label="关键提示" value={navData?.needs_ingest ? '需采集' : '可研究'} note="先确认数据质量，再进行曲线与组合判断" />
          </>
        }
      />

      <DetailSection title="基金概览" description="基本信息、数据质量门禁和研究评分">
        <FundBasicInfoCard fund={fund} />
        <FundDataQualityGate
          latestNavDate={latestNavDate}
          navQuality={navQuality}
          loading={navQualityLoading}
        />
        <PersonalResearchScoreCard score={personalResearchScore} />
      </DetailSection>

      <DetailSection title="净值与风险" description="观察收益曲线、风险状态和是否适合继续研究">
        <FundNavChartCard
          option={navChartOption}
          loading={navLoading}
          error={navError}
          hasRecords={Boolean(navData?.records?.length)}
          needsIngest={navData?.needs_ingest}
        />
        <FundDataQualitySnapshot navQuality={navQuality} loading={navQualityLoading} />
        <FundMetricsSummary performanceMetrics={performanceMetrics} feeData={feeData} />
      </DetailSection>

      <DetailSection title="持仓分布" description="查看底层持仓是否集中、是否适合纳入组合">
        <FundHoldingsDistribution holdingsData={holdingsData} loading={holdingsLoading} />
      </DetailSection>
    </div>
  );
}
