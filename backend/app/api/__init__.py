"""HTTP API layer.

`app.api.v1` hosts versioned routers. `app.api.middleware` and
`app.api.errors` provide framework plumbing (request-id injection,
exception handlers). The `router` exported here is the aggregate v1
router that `app.main` mounts under `/api/v1`.
"""

from __future__ import annotations

from app.api.v1 import router

__all__ = ["router"]
