import os
import unittest
import urllib.parse
from unittest.mock import patch

from karaoke.publish.oauth import (
    GOOGLE_AUTH_URL,
    GOOGLE_REVOKE_URL,
    GOOGLE_TOKEN_URL,
    YOUTUBE_SCOPE,
    GoogleOAuth,
    OAuthError,
    OAuthStateStore,
    build_authorization_url,
    code_challenge_for,
    redact,
)


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if not self._responses:
            raise AssertionError(f"Unexpected POST {url}")
        return self._responses.pop(0)


class OAuthTests(unittest.TestCase):
    def test_authorization_url_has_offline_consent_and_pkce(self):
        verifier = "verifier-for-test-1234567890"
        challenge = code_challenge_for(verifier)
        url = build_authorization_url(
            client_id="client-123",
            redirect_uri="http://127.0.0.1:8765/api/publish/oauth/callback",
            state="state-abc",
            code_challenge=challenge,
        )
        self.assertTrue(url.startswith(GOOGLE_AUTH_URL))
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(query["client_id"], ["client-123"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["scope"], [YOUTUBE_SCOPE])
        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(query["prompt"], ["consent"])
        self.assertEqual(query["state"], ["state-abc"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["code_challenge"], [challenge])
        self.assertNotIn("client_secret", query)

    def test_start_registers_single_use_state(self):
        oauth = GoogleOAuth(
            client_id="client-123",
            client_secret="secret",
            redirect_uri="http://127.0.0.1:8765/api/publish/oauth/callback",
        )
        url = oauth.authorization_url()
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        state = query["state"][0]
        pending = oauth.states.consume(state)
        self.assertTrue(pending.verifier)
        with self.assertRaises(OAuthError) as caught:
            oauth.states.consume(state)
        self.assertEqual(caught.exception.code, "invalid_state")

    def test_state_ttl_and_one_time_use(self):
        now = [1000.0]
        states = OAuthStateStore(ttl=10, clock=lambda: now[0])
        state = states.issue("verifier", "http://127.0.0.1/callback")
        self.assertEqual(len(states), 1)
        now[0] += 11
        with self.assertRaises(OAuthError) as caught:
            states.consume(state)
        self.assertEqual(caught.exception.code, "state_expired")
        self.assertEqual(len(states), 0)

    def test_exchange_code_sends_verifier_and_consumes_state(self):
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    {
                        "access_token": "access-1",
                        "refresh_token": "refresh-1",
                        "expires_in": 3600,
                        "scope": YOUTUBE_SCOPE,
                        "token_type": "Bearer",
                    },
                )
            ]
        )
        oauth = GoogleOAuth(
            client_id="client-123",
            client_secret="secret",
            redirect_uri="http://127.0.0.1:8765/api/publish/oauth/callback",
            session=session,
        )
        url = oauth.authorization_url()
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        state = query["state"][0]
        challenge = query["code_challenge"][0]

        tokens = oauth.exchange_code("auth-code", state)
        call = session.calls[0]
        self.assertEqual(call["url"], GOOGLE_TOKEN_URL)
        self.assertEqual(call["data"]["grant_type"], "authorization_code")
        self.assertEqual(call["data"]["code"], "auth-code")
        self.assertEqual(call["data"]["redirect_uri"], oauth.redirect_uri)
        self.assertEqual(
            code_challenge_for(call["data"]["code_verifier"]),
            challenge,
        )
        self.assertEqual(tokens["access_token"], "access-1")
        self.assertEqual(tokens["refresh_token"], "refresh-1")
        self.assertEqual(tokens["scopes"], [YOUTUBE_SCOPE])
        self.assertTrue(tokens["expires_at"])

        with self.assertRaises(OAuthError):
            oauth.exchange_code("auth-code", state)

    def test_refresh_preserves_refresh_token(self):
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    {
                        "access_token": "access-2",
                        "expires_in": 1800,
                        "scope": YOUTUBE_SCOPE,
                    },
                )
            ]
        )
        oauth = GoogleOAuth(
            client_id="client-123",
            client_secret="secret",
            session=session,
        )
        tokens = oauth.refresh("refresh-old")
        self.assertEqual(tokens["access_token"], "access-2")
        self.assertEqual(tokens["refresh_token"], "refresh-old")
        self.assertEqual(session.calls[0]["data"]["grant_type"], "refresh_token")

    def test_revoke_posts_token_and_tolerates_failure(self):
        session = FakeSession([FakeResponse(200, {}), FakeResponse(400, {})])
        oauth = GoogleOAuth(
            client_id="client-123",
            client_secret="secret",
            session=session,
        )
        self.assertTrue(oauth.revoke("refresh-secret"))
        self.assertEqual(session.calls[0]["url"], GOOGLE_REVOKE_URL)
        self.assertEqual(session.calls[0]["params"], {"token": "refresh-secret"})
        self.assertFalse(oauth.revoke("refresh-secret"))

    def test_missing_credentials_raise(self):
        with patch.dict(
            os.environ,
            {"GOOGLE_OAUTH_CLIENT_ID": "", "GOOGLE_OAUTH_CLIENT_SECRET": ""},
        ):
            oauth = GoogleOAuth(client_id="", client_secret="")
            with self.assertRaises(OAuthError) as caught:
                oauth.authorization_url()
        self.assertEqual(caught.exception.code, "missing_credentials")

    def test_redaction_removes_tokens_and_capability_urls(self):
        text = (
            "access_token=abc123&refresh_token=def456 "
            "Authorization: Bearer zzz "
            "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=cap"
        )
        cleaned = redact(text)
        for secret in ("abc123", "def456", "zzz", "cap"):
            self.assertNotIn(secret, cleaned)


if __name__ == "__main__":
    unittest.main()
