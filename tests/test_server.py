"""Tests for the HTTP server entry point."""
import os
import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def mock_servicenow_env(monkeypatch):
    """Set minimal environment for server to start."""
    monkeypatch.setenv("SERVICENOW_INSTANCE", "https://test.service-now.com")
    monkeypatch.setenv("SERVICENOW_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("SERVICENOW_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("SERVICENOW_TOOL_PROFILE", "tickets")


class TestCreateMCPServer:
    def test_creates_server(self):
        from server import create_mcp_server
        mcp = create_mcp_server()
        assert mcp.name == "sweetgreen-servicenow"

    def test_tickets_profile_registers_subset(self, monkeypatch):
        monkeypatch.setenv("SERVICENOW_TOOL_PROFILE", "tickets")
        from server import create_mcp_server
        mcp = create_mcp_server()
        assert mcp is not None

    def test_full_profile_registers_all(self, monkeypatch):
        monkeypatch.setenv("SERVICENOW_TOOL_PROFILE", "full")
        from server import create_mcp_server
        mcp = create_mcp_server()
        assert mcp is not None


class TestHealthEndpoint:
    def test_health_returns_200(self):
        from server import health_check
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        from starlette.routing import Route

        app = Starlette(routes=[Route("/health", health_check)])
        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "mcp-servicenow"
        assert data["transport"] == "streamable-http"
        assert data["tool_profile"] == "tickets"

    def test_health_reflects_profile(self, monkeypatch):
        monkeypatch.setenv("SERVICENOW_TOOL_PROFILE", "itsm")
        from server import health_check
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        from starlette.routing import Route

        app = Starlette(routes=[Route("/health", health_check)])
        client = TestClient(app)
        response = client.get("/health")

        assert response.json()["tool_profile"] == "itsm"


class TestOAuthCallbackEndpoint:
    def test_missing_params_returns_400(self):
        from server import servicenow_oauth_callback
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        from starlette.routing import Route

        app = Starlette(routes=[
            Route("/oauth/servicenow/callback", servicenow_oauth_callback),
        ])
        client = TestClient(app)

        # No code or state
        response = client.get("/oauth/servicenow/callback", follow_redirects=False)
        assert response.status_code == 400

        # Only code, no state
        response = client.get(
            "/oauth/servicenow/callback?code=abc",
            follow_redirects=False,
        )
        assert response.status_code == 400

        # Only state, no code
        response = client.get(
            "/oauth/servicenow/callback?state=xyz",
            follow_redirects=False,
        )
        assert response.status_code == 400
