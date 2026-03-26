"""Tests for the ServiceNow OAuth proxy provider."""
import asyncio
import time
import pytest

from servicenow_oauth_provider import (
    ServiceNowOAuthProvider,
    StoredAuthCode,
    StoredToken,
)


@pytest.fixture
def env_setup(monkeypatch):
    """Set environment for OAuth provider."""
    monkeypatch.setenv("SERVICENOW_INSTANCE", "https://test.service-now.com")
    monkeypatch.setenv("SERVICENOW_OAUTH_CLIENT_ID", "sn-client-id")
    monkeypatch.setenv("SERVICENOW_OAUTH_CLIENT_SECRET", "sn-client-secret")
    monkeypatch.setenv("MCP_SERVER_BASE_URL", "https://mcp.example.com")


@pytest.fixture
def provider(env_setup):
    return ServiceNowOAuthProvider()


def _run(coro):
    """Helper to run async functions in sync tests."""
    return asyncio.run(coro)


class TestStoredAuthCode:
    def test_not_expired_when_fresh(self):
        code = StoredAuthCode(
            servicenow_code="abc",
            client_id="test",
            redirect_uri="http://localhost",
            code_verifier=None,
        )
        assert not code.is_expired

    def test_expired_after_ttl(self):
        code = StoredAuthCode(
            servicenow_code="abc",
            client_id="test",
            redirect_uri="http://localhost",
            code_verifier=None,
            created_at=time.time() - 600,
            expires_in=300,
        )
        assert code.is_expired


class TestStoredToken:
    def test_not_expired_when_fresh(self):
        token = StoredToken(
            servicenow_access_token="sn-token",
            servicenow_refresh_token=None,
            client_id="test",
        )
        assert not token.is_expired

    def test_expired_after_ttl(self):
        token = StoredToken(
            servicenow_access_token="sn-token",
            servicenow_refresh_token=None,
            client_id="test",
            created_at=time.time() - 3600,
            expires_in=1800,
        )
        assert token.is_expired


class TestClientRegistration:
    def test_register_and_get_client(self, provider):
        from mcp.shared.auth import OAuthClientInformationFull

        client_info = OAuthClientInformationFull(
            client_id="test-client",
            client_secret="secret",
            redirect_uris=["http://localhost/callback"],
        )
        _run(provider.register_client(client_info))

        result = _run(provider.get_client("test-client"))
        assert result is not None
        assert result.client_id == "test-client"

    def test_get_unknown_client(self, provider):
        result = _run(provider.get_client("nonexistent"))
        assert result is None


class TestTokenValidation:
    def test_load_valid_token(self, provider):
        token = StoredToken(
            servicenow_access_token="sn-abc",
            servicenow_refresh_token=None,
            client_id="test",
        )
        provider._access_tokens["mcp-token-123"] = token

        result = _run(provider.load_access_token("mcp-token-123"))
        assert result is not None
        assert result.servicenow_access_token == "sn-abc"

    def test_load_expired_token_returns_none(self, provider):
        token = StoredToken(
            servicenow_access_token="sn-abc",
            servicenow_refresh_token=None,
            client_id="test",
            created_at=time.time() - 3600,
            expires_in=1800,
        )
        provider._access_tokens["mcp-token-expired"] = token

        result = _run(provider.load_access_token("mcp-token-expired"))
        assert result is None
        assert "mcp-token-expired" not in provider._access_tokens

    def test_load_unknown_token_returns_none(self, provider):
        result = _run(provider.load_access_token("nonexistent"))
        assert result is None


class TestServiceNowTokenMapping:
    def test_get_servicenow_token(self, provider):
        token = StoredToken(
            servicenow_access_token="sn-real-token",
            servicenow_refresh_token=None,
            client_id="test",
        )
        provider._access_tokens["mcp-token"] = token

        sn_token = provider.get_servicenow_token_for_mcp_token("mcp-token")
        assert sn_token == "sn-real-token"

    def test_expired_token_returns_none(self, provider):
        token = StoredToken(
            servicenow_access_token="sn-old-token",
            servicenow_refresh_token=None,
            client_id="test",
            created_at=time.time() - 3600,
            expires_in=1800,
        )
        provider._access_tokens["mcp-token"] = token

        sn_token = provider.get_servicenow_token_for_mcp_token("mcp-token")
        assert sn_token is None


class TestCleanup:
    def test_cleanup_removes_expired(self, provider):
        expired_token = StoredToken(
            servicenow_access_token="old",
            servicenow_refresh_token=None,
            client_id="test",
            created_at=time.time() - 7200,
            expires_in=1800,
        )
        provider._access_tokens["expired-1"] = expired_token

        valid_token = StoredToken(
            servicenow_access_token="fresh",
            servicenow_refresh_token=None,
            client_id="test",
        )
        provider._access_tokens["valid-1"] = valid_token

        count = provider.cleanup_expired()
        assert count == 1
        assert "expired-1" not in provider._access_tokens
        assert "valid-1" in provider._access_tokens


class TestAuthorize:
    def test_authorize_returns_servicenow_url(self, provider):
        from mcp.shared.auth import OAuthClientInformationFull
        from mcp.server.auth.provider import AuthorizationParams
        from pydantic import AnyHttpUrl

        client = OAuthClientInformationFull(
            client_id="claude-client",
            client_secret="secret",
            redirect_uris=["https://claude.ai/callback"],
        )

        params = AuthorizationParams(
            redirect_uri=AnyHttpUrl("https://claude.ai/callback"),
            state="claude-state-123",
            scopes=[],
            code_challenge="challenge",
            redirect_uri_provided_explicitly=True,
        )

        url = _run(provider.authorize(client, params))

        assert "test.service-now.com/oauth_auth.do" in url
        assert "response_type=code" in url
        assert "client_id=sn-client-id" in url
        assert "redirect_uri=" in url
        assert "state=" in url
