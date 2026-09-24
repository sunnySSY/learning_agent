"""Bearer authentication, request IDs and small single-process rate limiter."""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from uuid import UUID, uuid4

import jwt
from fastapi import HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import cfg


bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    subject: str
    user_id: str
    token_mode: str


class Authenticator:
    def __init__(self, repository):
        self.repository = repository
        self.token_map: dict[str, str] = {}
        raw = cfg.api_bearer_tokens
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            if "=" in item:
                token, subject = item.split("=", 1)
            elif ":" in item:
                token, subject = item.split(":", 1)
            else:
                token, subject = item, item
            self.token_map[token.strip()] = subject.strip()

    def authenticate(self, credentials: HTTPAuthorizationCredentials | None) -> Principal:
        if not credentials or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要 Bearer 凭证", headers={"WWW-Authenticate": "Bearer"})
        token = credentials.credentials
        subject: str | None = None
        mode = cfg.auth_mode
        if mode == "dev":
            # The only accepted development identity is explicit and local.
            subject = self.token_map.get(token) or ("local-dev-user" if token == "dev-token" else None)
        elif mode == "token":
            subject = self.token_map.get(token)
        elif mode == "jwt":
            try:
                options = {"verify_aud": bool(cfg.auth_audience), "verify_iss": bool(cfg.auth_issuer), "require": ["exp", "sub"]}
                if cfg.auth_issuer: options["require"].append("iss")
                if cfg.auth_audience: options["require"].append("aud")
                if cfg.auth_secret:
                    key = cfg.auth_secret; algorithms = ["HS256"]
                elif cfg.auth_jwks_url:
                    key = jwt.PyJWKClient(cfg.auth_jwks_url).get_signing_key_from_jwt(token).key; algorithms = ["RS256", "ES256"]
                else:
                    raise ValueError("没有 JWT 验证密钥")
                claims = jwt.decode(token, key, algorithms=algorithms, audience=cfg.auth_audience or None, issuer=cfg.auth_issuer or None, options=options)
                subject = str(claims.get("sub") or "")
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=401, detail="凭证无效", headers={"WWW-Authenticate": "Bearer"}) from exc
        if not subject:
            raise HTTPException(status_code=401, detail="凭证无效", headers={"WWW-Authenticate": "Bearer"})
        try:
            user_id = self.repository.ensure_user(subject)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="用户已停用") from exc
        return Principal(subject=subject, user_id=user_id, token_mode=mode)


def request_id(request: Request) -> str:
    value = request.headers.get("X-Request-ID", "")
    try:
        UUID(value)
        return value
    except (ValueError, AttributeError):
        return str(uuid4())


def ip_digest(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    return hashlib.sha256(host.encode()).hexdigest()[:16]


class SlidingWindowLimiter:
    """In-memory limiter for the documented single-instance dev backend."""

    def __init__(self):
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, window: int = 60) -> tuple[bool, int, int]:
        now = time.monotonic(); q = self._events[key]
        while q and q[0] <= now - window:
            q.popleft()
        remaining = max(0, limit - len(q) - 1)
        if len(q) >= limit:
            retry = max(1, int(q[0] + window - now))
            return False, 0, retry
        q.append(now)
        return True, remaining, 0
