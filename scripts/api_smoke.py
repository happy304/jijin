"""Local API smoke checks for a running backend.

Usage:
    python scripts/api_smoke.py --base-url http://127.0.0.1:8000

The script intentionally avoids mutating state. It checks lightweight endpoints
that should stay responsive during local development and before releases.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass
class CheckResult:
    name: str
    ok: bool
    status: int | None = None
    detail: str = ""


def _get_json(base_url: str, path: str, timeout: float) -> tuple[int, Any]:
    request = Request(f"{base_url.rstrip('/')}{path}", headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - local developer smoke script
        raw = response.read().decode("utf-8")
        return response.status, json.loads(raw) if raw else None


def _check(base_url: str, name: str, path: str, timeout: float) -> CheckResult:
    try:
        status, payload = _get_json(base_url, path, timeout)
    except HTTPError as exc:
        return CheckResult(name=name, ok=False, status=exc.code, detail=str(exc))
    except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return CheckResult(name=name, ok=False, detail=str(exc))
    return CheckResult(
        name=name,
        ok=200 <= status < 300,
        status=status,
        detail="ok" if payload is not None else "empty response",
    )


def run(base_url: str, timeout: float) -> list[CheckResult]:
    checks = [
        ("health", "/health"),
        ("api version", "/api/v1/version"),
        ("advisor config", "/api/v1/advisor/config"),
        ("advisor health", "/api/v1/advisor/health"),
        ("backtests list", "/api/v1/backtests?page=1&page_size=1"),
    ]
    return [_check(base_url, name, path, timeout) for name, path in checks]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local API smoke checks.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    results = run(args.base_url, args.timeout)
    for result in results:
        mark = "PASS" if result.ok else "FAIL"
        status = result.status if result.status is not None else "-"
        print(f"[{mark}] {result.name:<16} status={status} {result.detail}")
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
