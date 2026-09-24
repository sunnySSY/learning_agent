"""Phase 5 application layer.

The package keeps HTTP/auth/file-job concerns outside the CLI and outside the
domain packages (``memory`` and ``rag``).  ``create_app`` is intentionally a
factory so tests and deployments can supply isolated configuration.
"""

from .api import create_app

__all__ = ["create_app"]
