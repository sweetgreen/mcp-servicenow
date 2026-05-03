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
            assert "missing variable" in result

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
            assert "abc" in result

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


class TestBuildAccessRequestVariables:
    def test_returns_dict_with_all_eleven_keys(self):
        from Table_Tools.service_catalog_tools import _build_access_request_variables
        result = _build_access_request_variables(
            application_sys_id="appsysid",
            access_level="Administrator",
            justification="Need it",
            request_type="new_user",
        )
        expected_keys = {
            "what_can_we_help_you_with",
            "request_type",
            "is_the_request_for_you_or_someone_else",
            "cat_requested_for",
            "select_application",
            "describe_access_level_needed_in_selected_system",
            "describe_your_request",
            "business_justification",
            "vs_cc_multi_select_summary",
            "cc_summary",
            "cc_set",
        }
        assert set(result.keys()) == expected_keys

    def test_cc_fields_are_empty_strings(self):
        from Table_Tools.service_catalog_tools import _build_access_request_variables
        result = _build_access_request_variables("a", "Admin", "j", "new_user")
        assert result["cc_summary"] == ""
        assert result["cc_set"] == ""
        assert result["vs_cc_multi_select_summary"] == ""
        assert result["cat_requested_for"] == ""

    def test_application_sys_id_in_select_application(self):
        from Table_Tools.service_catalog_tools import _build_access_request_variables
        result = _build_access_request_variables("APPSYSID", "Admin", "j", "new_user")
        assert result["select_application"] == "APPSYSID"

    def test_justification_populates_two_fields(self):
        from Table_Tools.service_catalog_tools import _build_access_request_variables
        result = _build_access_request_variables("a", "Admin", "Because reasons", "new_user")
        assert result["describe_your_request"] == "Because reasons"
        assert result["business_justification"] == "Because reasons"

    def test_request_type_passed_through(self):
        from Table_Tools.service_catalog_tools import _build_access_request_variables
        for req_type in ["new_user", "modify", "remove"]:
            result = _build_access_request_variables("a", "Admin", "j", req_type)
            assert result["request_type"] == req_type

    def test_top_level_category_constant(self):
        from Table_Tools.service_catalog_tools import _build_access_request_variables
        result = _build_access_request_variables("a", "Admin", "j", "new_user")
        assert result["what_can_we_help_you_with"] == "Access to Application"
        assert result["is_the_request_for_you_or_someone_else"] == "myself"


class TestResolveUser:
    @pytest.mark.asyncio
    async def test_sys_id_passthrough(self):
        from Table_Tools.service_catalog_tools import _resolve_user
        sid = "a" * 32
        with patch("Table_Tools.service_catalog_tools.make_nws_request", new=AsyncMock()) as m:
            result = await _resolve_user(sid)
            assert result == sid
            m.assert_not_called()

    @pytest.mark.asyncio
    async def test_email_lookup_happy_path(self):
        from Table_Tools.service_catalog_tools import _resolve_user
        with patch(
            "Table_Tools.service_catalog_tools.make_nws_request",
            new=AsyncMock(return_value={"result": [{"sys_id": "USR1"}]}),
        ) as m:
            result = await _resolve_user("alice@example.com")
            assert result == "USR1"
            assert "email=alice@example.com" in m.call_args_list[0][0][0]

    @pytest.mark.asyncio
    async def test_user_name_fallback_when_email_empty(self):
        from Table_Tools.service_catalog_tools import _resolve_user
        responses = [
            {"result": []},
            {"result": [{"sys_id": "USR2"}]},
        ]
        with patch(
            "Table_Tools.service_catalog_tools.make_nws_request",
            new=AsyncMock(side_effect=responses),
        ) as m:
            result = await _resolve_user("alice")
            assert result == "USR2"
            assert "user_name=alice" in m.call_args_list[1][0][0]

    @pytest.mark.asyncio
    async def test_not_found_returns_error(self):
        from Table_Tools.service_catalog_tools import _resolve_user
        from constants import ERROR_USER_NOT_FOUND
        with patch(
            "Table_Tools.service_catalog_tools.make_nws_request",
            new=AsyncMock(return_value={"result": []}),
        ):
            result = await _resolve_user("ghost@example.com")
            assert result == ERROR_USER_NOT_FOUND.format(identifier="ghost@example.com")

    @pytest.mark.asyncio
    async def test_ambiguous_returns_error(self):
        from Table_Tools.service_catalog_tools import _resolve_user
        with patch(
            "Table_Tools.service_catalog_tools.make_nws_request",
            new=AsyncMock(return_value={"result": [{"sys_id": "U1"}, {"sys_id": "U2"}]}),
        ):
            result = await _resolve_user("dup@example.com")
            assert "Multiple users" in result
            assert "U1" in result and "U2" in result
