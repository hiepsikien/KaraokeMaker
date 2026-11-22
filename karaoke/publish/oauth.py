"""Google OAuth 2.0 for a desktop/loopback app.

Only the public OAuth endpoints are called (no Google client library). The
authorization request always asks for ``access_type=offline`` and
``prompt=consent`` so Google returns a refresh token. ``state`` values are
random, single-use and expire after ten minutes; the PKCE verifier is kept in
memory next to the state and never written to disk.

No function in this module logs a client secret, authorization code, access
token, refresh token or capability URL.
"""

from __future__ import annotations

import base64
import hashlib
import html
import logging
import os
import re
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

import requests

from karaoke.publish.models import plus_seconds_iso

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
YOUTUBE_SCOPES = (YOUTUBE_SCOPE,)
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/api/publish/oauth/callback"
STATE_TTL_SECONDS = 10 * 60


class OAuthError(RuntimeError):
    """OAuth failed; ``code`` is a stable machine-readable identifier."""

    def __init__(self, message: str, *, code: str = "oauth_error", description: str = ""):
        super().__init__(message)
        self.code = code
        self.description = description


@dataclass
class PendingState:
    state: str
    verifier: str
    redirect_uri: str
    provider: str
    created: float


class OAuthStateStore:
    """Thread-safe, one-time, TTL-limited state/code-verifier storage."""

    def __init__(self, *, ttl: float = STATE_TTL_SECONDS, clock=time.time):
        self.ttl = float(ttl)
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: dict[str, PendingState] = {}

    def issue(
        self,
        verifier: str,
        redirect_uri: str,
        *,
        provider: str = "youtube",
    ) -> str:
        state = secrets.token_urlsafe(32)
        pending = PendingState(
            state=state,
            verifier=verifier,
            redirect_uri=redirect_uri,
            provider=provider,
            created=float(self._clock()),
        )
        with self._lock:
            self._purge_locked()
            self._pending[state] = pending
        return state

    def consume(self, state: str) -> PendingState:
        """Return and remove a pending state. Expired/unknown states fail."""
        key = str(state or "").strip()
        with self._lock:
            pending = self._pending.pop(key, None)
            if pending is None:
                raise OAuthError(
                    "Phiên kết nối không hợp lệ hoặc đã dùng. Hãy bấm Kết nối lại.",
                    code="invalid_state",
                )
            if float(self._clock()) - pending.created > self.ttl:
                raise OAuthError(
                    "Phiên kết nối đã hết hạn. Hãy bấm Kết nối lại.",
                    code="state_expired",
                )
            return pending

    def clear(self) -> None:
        with self._lock:
            self._pending.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._pending)

    def _purge_locked(self) -> None:
        cutoff = float(self._clock()) - self.ttl
        expired = [key for key, item in self._pending.items() if item.created < cutoff]
        for key in expired:
            self._pending.pop(key, None)


def _b64url(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def new_code_verifier() -> str:
    """RFC 7636 verifier: 64 random bytes encoded as URL-safe text."""
    return secrets.token_urlsafe(64)


def code_challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return _b64url(digest)


def build_authorization_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    scopes: tuple[str, ...] | list[str] = YOUTUBE_SCOPES,
) -> str:
    """Pure URL builder (easy to test without environment/network)."""
    if not str(client_id or "").strip():
        raise OAuthError("Thiếu GOOGLE_OAUTH_CLIENT_ID.", code="missing_client_id")
    if not str(redirect_uri or "").strip():
        raise OAuthError("Thiếu GOOGLE_OAUTH_REDIRECT_URI.", code="missing_redirect_uri")
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


def normalize_token_response(
    payload: Any,
    *,
    refresh_token: str | None = None,
    default_scopes: tuple[str, ...] | list[str] = (),
) -> dict:
    """Convert Google's token response into our stored token shape."""
    data = payload if isinstance(payload, dict) else {}
    access_token = str(data.get("access_token") or "").strip()
    if not access_token:
        raise OAuthError("Google không trả access token.", code="missing_access_token")
    raw_expires = data.get("expires_in")
    try:
        expires_in = float(raw_expires) if raw_expires is not None else 3600.0
    except (TypeError, ValueError):
        expires_in = 3600.0
    raw_scope = str(data.get("scope") or "").strip()
    scopes = raw_scope.split() if raw_scope else list(default_scopes)
    return {
        "access_token": access_token,
        "refresh_token": str(data.get("refresh_token") or refresh_token or "").strip(),
        "expires_at": plus_seconds_iso(expires_in),
        "scopes": scopes,
        "token_type": str(data.get("token_type") or "Bearer"),
    }


_REDACT_PATTERNS = (
    re.compile(
        r"(?i)((?:access_token|refresh_token|id_token|client_secret|code_verifier|upload_url|upload_token|upload_id|publish_id)=)([^&\s\"']+)"
    ),
    re.compile(r"(?i)(authorization:\s*bearer\s+)(\S+)"),
    re.compile(r"https://[^\s\"']*googleapis\.com/upload/[^\s\"']+"),
)


def redact(text: Any) -> str:
    """Remove credentials/capability URLs before text reaches a log or API."""
    value = str(text or "")
    for pattern in _REDACT_PATTERNS:
        if pattern.groups >= 2:
            value = pattern.sub(r"\1<redacted>", value)
        else:
            value = pattern.sub("<redacted>", value)
    return value


class GoogleOAuth:
    """Loopback OAuth client. Credentials come from the environment by default."""

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        redirect_uri: str | None = None,
        *,
        states: OAuthStateStore | None = None,
        session: Any | None = None,
        clock=time.time,
        timeout: float = 30.0,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self.states = states or OAuthStateStore(clock=clock)
        self._session = session
        self._timeout = float(timeout)

    # ---------------------------------------------------------- credentials
    @property
    def client_id(self) -> str:
        return str(self._client_id or os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")).strip()

    @property
    def client_secret(self) -> str:
        return str(self._client_secret or os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")).strip()

    @property
    def redirect_uri(self) -> str:
        return str(
            self._redirect_uri
            or os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "")
            or DEFAULT_REDIRECT_URI
        ).strip()

    def _require_credentials(self) -> None:
        if not self.client_id or not self.client_secret:
            raise OAuthError(
                "Chưa cấu hình GOOGLE_OAUTH_CLIENT_ID/SECRET trong .env.",
                code="missing_credentials",
            )

    def _http(self):
        return self._session if self._session is not None else requests

    # -------------------------------------------------------------- flow
    def start(self, *, redirect_uri: str | None = None) -> dict:
        """Create state + PKCE and return the consent URL and bookkeeping."""
        self._require_credentials()
        target = str(redirect_uri or self.redirect_uri).strip()
        verifier = new_code_verifier()
        state = self.states.issue(verifier, target, provider="youtube")
        challenge = code_challenge_for(verifier)
        url = build_authorization_url(
            client_id=self.client_id,
            redirect_uri=target,
            state=state,
            code_challenge=challenge,
        )
        return {
            "url": url,
            "state": state,
            "redirect_uri": target,
            "code_challenge": challenge,
        }

    def authorization_url(self) -> str:
        return self.start()["url"]

    def exchange_code(self, code: str, state: str) -> dict:
        """Consume ``state`` and exchange an authorization code for tokens."""
        self._require_credentials()
        if not str(code or "").strip():
            raise OAuthError("Google không trả authorization code.", code="missing_code")
        pending = self.states.consume(state)
        response = self._http().post(
            GOOGLE_TOKEN_URL,
            data={
                "code": str(code).strip(),
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": pending.redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": pending.verifier,
            },
            timeout=self._timeout,
        )
        return normalize_token_response(
            self._json_or_error(response, action="đổi mã đăng nhập"),
            default_scopes=YOUTUBE_SCOPES,
        )

    def refresh(self, refresh_token: str) -> dict:
        """Refresh an access token, preserving the existing refresh token."""
        self._require_credentials()
        token = str(refresh_token or "").strip()
        if not token:
            raise OAuthError("Không có refresh token.", code="missing_refresh_token")
        response = self._http().post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": token,
                "grant_type": "refresh_token",
            },
            timeout=self._timeout,
        )
        return normalize_token_response(
            self._json_or_error(response, action="làm mới token"),
            refresh_token=token,
            default_scopes=YOUTUBE_SCOPES,
        )

    def revoke(self, token: str) -> bool:
        """Revoke a refresh/access token. Never raises for remote failures."""
        value = str(token or "").strip()
        if not value:
            return False
        try:
            response = self._http().post(
                GOOGLE_REVOKE_URL,
                params={"token": value},
                timeout=self._timeout,
            )
        except Exception:  # requests errors can contain the token in their URL
            logger.warning("Google token revoke request failed.")
            return False
        if 200 <= int(getattr(response, "status_code", 0)) < 300:
            return True
        logger.warning(
            "Google token revoke failed with status %s.",
            getattr(response, "status_code", "unknown"),
        )
        return False

    # -------------------------------------------------------------- helpers
    def _json_or_error(self, response: Any, *, action: str) -> dict:
        status = int(getattr(response, "status_code", 0) or 0)
        try:
            payload = response.json()
        except Exception:
            payload = {}
        if status >= 400 or not isinstance(payload, dict):
            error = str(payload.get("error") or "").strip() if isinstance(payload, dict) else ""
            description = str(payload.get("error_description") or "").strip() if isinstance(payload, dict) else ""
            # Only Google's error codes are logged, never form data/tokens.
            logger.warning("Google OAuth %s failed: %s", action, error or status)
            detail = error or f"HTTP {status}"
            raise OAuthError(
                f"Không {action} được với Google ({redact(detail)}).",
                code=error or "token_request_failed",
                description=redact(description),
            )
        error = str(payload.get("error") or "").strip()
        if error:
            logger.warning("Google OAuth %s failed: %s", action, error)
            raise OAuthError(
                f"Không {action} được với Google ({redact(error)}).",
                code=error,
                description=redact(payload.get("error_description")),
            )
        return payload


def callback_html(*, ok: bool, message: str) -> str:
    """Minimal self-closing callback page (no external assets)."""
    safe = html.escape(str(message or ""), quote=True)
    title = "Đã kết nối YouTube" if ok else "Không kết nối được YouTube"
    tone = "#1f7a3d" if ok else "#a33124"
    return (
        "<!doctype html><html lang='vi'><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title>"
        "<body style='font-family:system-ui,sans-serif;padding:3rem;line-height:1.5'>"
        f"<h1 style='color:{tone}'>{html.escape(title)}</h1>"
        f"<p>{safe}</p><p>Bạn có thể đóng tab này và quay lại KaraokeMaker.</p>"
        "<script>setTimeout(function(){window.close()},1500)</script>"
        "</body></html>"
    )


__all__ = [
    "DEFAULT_REDIRECT_URI",
    "GOOGLE_AUTH_URL",
    "GOOGLE_REVOKE_URL",
    "GOOGLE_TOKEN_URL",
    "OAuthError",
    "OAuthStateStore",
    "PendingState",
    "GoogleOAuth",
    "build_authorization_url",
    "callback_html",
    "code_challenge_for",
    "new_code_verifier",
    "normalize_token_response",
    "redact",
    "YOUTUBE_SCOPE",
    "YOUTUBE_SCOPES",
]
