"""Celery task package for the Fund Quant Platform.

Importing this package eagerly loads the Celery application instance so
that

    celery -A app.tasks worker ...
    celery -A app.tasks beat ...

resolves ``app.tasks:celery_app`` without any extra module hint. The
production compose file (``deploy/docker-compose.yml``) points at the
more explicit ``app.tasks.celery_app`` module path; both are supported.

Actual task modules (``ping``, ``ingest``, ``backtest``, ...) are loaded
lazily through Celery's ``autodiscover_tasks`` or by being imported from
:mod:`app.tasks.celery_app` after the app is constructed.
"""

from __future__ import annotations

from app.tasks.celery_app import celery_app

# ``app`` is the conventional attribute name the Celery CLI looks for
# when it is given only the package (``-A app.tasks``). Re-exporting it
# here lets both of the following invocations work::
#
#     celery -A app.tasks          worker ...
#     celery -A app.tasks.celery_app worker ...
app = celery_app

__all__ = ["app", "celery_app"]
