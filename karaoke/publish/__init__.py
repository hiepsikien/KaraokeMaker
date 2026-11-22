"""YouTube publishing (Phase 5A).

The package is deliberately independent from the render pipeline:

* :mod:`karaoke.publish.models`    – persisted records and job states
* :mod:`karaoke.publish.store`     – atomic, locked JSON storage
* :mod:`karaoke.publish.oauth`     – Google OAuth 2.0 desktop/loopback + PKCE
* :mod:`karaoke.publish.youtube`   – YouTube Data API v3 REST adapter
* :mod:`karaoke.publish.worker`    – one-thread queue with retry/recovery
* :mod:`karaoke.publish.service`   – API-facing operations
* :mod:`karaoke.publish.router`    – FastAPI router (not registered by default)

TikTok is intentionally not implemented (backlog Phase 5B).
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
