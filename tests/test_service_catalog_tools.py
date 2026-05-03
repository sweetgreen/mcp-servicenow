"""Tests for service_catalog_tools.py — Service Catalog ordering and introspection."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import httpx

from Table_Tools.service_catalog_tools import _make_authenticated_request
from constants import (
    ERROR_CATALOG_AUTH_FAILED,
    ERROR_CATALOG_ACCESS_DENIED,
    ERROR_CATALOG_INVALID_REQUEST,
    ERROR_CATALOG_ITEM_NOT_FOUND,
    ERROR_CATALOG_ORDER_FAILED,
)


def _http_error(status_code: int, body: str = "") -> httpx.HTTPStatusError:
    """Build a fake HTTPStatusError for testing."""
    request = httpx.Request("POST", "https://example/api")
    response = httpx.Response(status_code=status_code, text=body, request=request)
    return httpx.HTTPStatusError("err", request=request, response=response)


class TestMakeAuthenticatedRequest:
    """Test _make_authenticated_request error mapping."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_result(self):
        with patch(
            "Table_Tools.service_catalog_tools._get_authenticated_headers",
            new=AsyncMock(return_value={"Authorization": "Bearer t"}),
        ), patch("httpx.AsyncClient") as MockClient:
            mock_client = MockClient.return_value.__aenter__.return_value
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json.return_value = {"result": {"sys_id": "abc", "number": "REQ1"}}
            mock_client.request = AsyncMock(return_value=mock_response)

            result = await _make_authenticated_request("POST", "https://x/api", {"k": "v"})

            assert result == {"sys_id": "abc", "number": "REQ1"}

    @pytest.mark.asyncio
    async def test_401_returns_auth_failed(self):
        with patch(
            "Table_Tools.service_catalog_tools._get_authenticated_headers",
            new=AsyncMock(return_value={}),
        ), patch("httpx.AsyncClient") as MockClient:
            mock_client = MockClient.return_value.__aenter__.return_value
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock(side_effect=_http_error(401))
            mock_client.request = AsyncMock(return_value=mock_response)

            result = await _make_authenticated_request("POST", "https://x/api", {})
            assert result == ERROR_CATALOG_AUTH_FAILED

    @pytest.mark.asyncio
    async def test_403_returns_access_denied(self):
        with patch(
            "Table_Tools.service_catalog_tools._get_authenticated_headers",
            new=AsyncMock(return_value={}),
        ), patch("httpx.AsyncClient") as MockClient:
            mock_client = MockClient.return_value.__aenter__.return_value
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock(side_effect=_http_error(403))
            mock_client.request = AsyncMock(return_value=mock_response)

            result = await _make_authenticated_request("POST", "https://x/api", {})
            assert result == ERROR_CATALOG_ACCESS_DENIED

    @pytest.mark.asyncio
    async def test_400_includes_response_body(self):
        with patch(
            "Table_Tools.service_catalog_tools._get_authenticated_headers",
            new=AsyncMock(return_value={}),
        ), patch("httpx.AsyncClient") as MockClient:
            mock_client = MockClient.return_value.__aenter__.return_value
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock(
                side_effect=_http_error(400, '{"error":{"message":"missing variable"}}')
            )
            mock_client.request = AsyncMock(return_value=mock_response)

            result = await _make_authenticated_request("POST", "https://x/api", {})
            assert "missing variable" in result or "ServiceNow rejected" in result

    @pytest.mark.asyncio
    async def test_404_returns_item_not_found(self):
        with patch(
            "Table_Tools.service_catalog_tools._get_authenticated_headers",
            new=AsyncMock(return_value={}),
        ), patch("httpx.AsyncClient") as MockClient:
            mock_client = MockClient.return_value.__aenter__.return_value
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock(side_effect=_http_error(404))
            mock_client.request = AsyncMock(return_value=mock_response)

            result = await _make_authenticated_request("POST", "https://x/items/abc/order_now", {})
            assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_500_returns_order_failed(self):
        with patch(
            "Table_Tools.service_catalog_tools._get_authenticated_headers",
            new=AsyncMock(return_value={}),
        ), patch("httpx.AsyncClient") as MockClient:
            mock_client = MockClient.return_value.__aenter__.return_value
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock(side_effect=_http_error(500))
            mock_client.request = AsyncMock(return_value=mock_response)

            result = await _make_authenticated_request("POST", "https://x/api", {})
            assert "failed" in result.lower()
