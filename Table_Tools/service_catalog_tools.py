"""ServiceNow Service Catalog tools — order catalog items, introspect variables."""

from __future__ import annotations

from typing import Any, Dict, Optional
import httpx
import orjson

from service_now_api_oauth import NWS_API_BASE

from constants import (
    ERROR_CATALOG_AUTH_FAILED,
    ERROR_CATALOG_ACCESS_DENIED,
    ERROR_CATALOG_INVALID_REQUEST,
    ERROR_CATALOG_ITEM_NOT_FOUND,
    ERROR_CATALOG_ORDER_FAILED,
)


async def _get_authenticated_headers() -> Dict[str, str]:
    """Get headers with OAuth authentication."""
    from oauth_client import get_oauth_client
    oauth_client = get_oauth_client()
    return await oauth_client.get_auth_headers()


def _handle_http_error(error: httpx.HTTPStatusError, url: str) -> str:
    """Map HTTP errors to user-facing strings."""
    status = error.response.status_code
    body = error.response.text or ""

    if status == 401:
        return ERROR_CATALOG_AUTH_FAILED
    if status == 403:
        return ERROR_CATALOG_ACCESS_DENIED
    if status == 400:
        # Try to extract ServiceNow error.message; fall back to raw body
        detail = body
        try:
            parsed = orjson.loads(body)
            detail = parsed.get("error", {}).get("message") or body
        except (orjson.JSONDecodeError, ValueError):
            pass
        return ERROR_CATALOG_INVALID_REQUEST.format(detail=detail)
    if status == 404:
        # Extract sys_id from URL if present (.../items/{sys_id}/order_now or .../items/{sys_id})
        sys_id = "unknown"
        parts = url.split("/items/")
        if len(parts) > 1:
            sys_id = parts[1].split("/")[0]
        return ERROR_CATALOG_ITEM_NOT_FOUND.format(sys_id=sys_id)
    return ERROR_CATALOG_ORDER_FAILED.format(detail=f"HTTP {status}")


async def _make_authenticated_request(
    method: str,
    url: str,
    json_data: Optional[Dict] = None,
) -> Dict[str, Any] | str:
    """Make an authenticated HTTP request. Returns result dict on success, error string on failure."""
    headers = await _get_authenticated_headers()

    async with httpx.AsyncClient(verify=True) as client:
        try:
            response = await client.request(method, url, json=json_data, headers=headers, timeout=30.0)
            response.raise_for_status()
            payload = response.json()
            if payload and "result" in payload:
                return payload["result"]
            return payload or {}
        except httpx.HTTPStatusError as e:
            return _handle_http_error(e, url)
        except Exception as e:
            return ERROR_CATALOG_ORDER_FAILED.format(detail=str(e))


def _build_access_request_variables(
    application_sys_id: str,
    access_level: str,
    justification: str,
    request_type: str,
) -> Dict[str, str]:
    """Build the variables dict for the access-request catalog item.
    Mirrors the captured browser payload exactly, including empty CC/watcher fields."""
    return {
        "what_can_we_help_you_with": "Access to Application",
        "request_type": request_type,
        "is_the_request_for_you_or_someone_else": "myself",
        "cat_requested_for": "",
        "select_application": application_sys_id,
        "describe_access_level_needed_in_selected_system": access_level,
        "describe_your_request": justification,
        "business_justification": justification,
        "vs_cc_multi_select_summary": "",
        "cc_summary": "",
        "cc_set": "",
    }
