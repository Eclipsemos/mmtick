"""Authentication boundary for sensitive live-account read endpoints."""

from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path

from fastapi import HTTPException, Request, Response, status

COOKIE_NAME = "mmtick_live_session"
SESSION_SECONDS = 8 * 60 * 60


class LiveAccess:
    def __init__(self, token_path: Path | None):
        self.token_path = token_path
        self._token = self._read_token(token_path)

    @property
    def configured(self) -> bool:
        return self._token is not None

    def verify_token(self, candidate: str) -> bool:
        return self._token is not None and hmac.compare_digest(candidate, self._token)

    def authorized(self, request: Request) -> bool:
        value = request.cookies.get(COOKIE_NAME)
        if not value or self._token is None:
            return False
        try:
            expires_text, signature = value.split(".", 1)
            expires = int(expires_text)
        except (TypeError, ValueError):
            return False
        if expires < int(time.time()):
            return False
        expected = hmac.new(
            self._token.encode(), expires_text.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature, expected)

    def require(self, request: Request) -> None:
        if not self.configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="LIVE operator access is not configured",
            )
        if not self.authorized(request):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="LIVE operator authentication required",
            )

    def establish(self, response: Response, request: Request) -> None:
        if self._token is None:
            raise RuntimeError("LIVE operator access is not configured")
        expires = int(time.time()) + SESSION_SECONDS
        expires_text = str(expires)
        signature = hmac.new(
            self._token.encode(), expires_text.encode(), hashlib.sha256
        ).hexdigest()
        forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0]
        response.set_cookie(
            COOKIE_NAME,
            f"{expires_text}.{signature}",
            max_age=SESSION_SECONDS,
            httponly=True,
            secure=request.url.scheme == "https" or forwarded_proto == "https",
            samesite="strict",
            path="/api/live",
        )

    @staticmethod
    def is_loopback(request: Request) -> bool:
        host = request.client.host if request.client else ""
        return host in {"127.0.0.1", "::1", "localhost", "testclient"}

    @staticmethod
    def _read_token(path: Path | None) -> str | None:
        if path is None or not path.exists():
            return None
        if path.stat().st_mode & 0o077:
            return None
        value = path.read_text().strip()
        return value if len(value) >= 32 else None
