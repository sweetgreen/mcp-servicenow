"""ServiceNow Service Catalog tools — order catalog items, introspect variables."""

from __future__ import annotations

from typing import Any, Dict, Optional
import re
import httpx
import orjson

from service_now_api_oauth import NWS_API_BASE, make_nws_request

from constants import (
    ACCESS_REQUEST_CATALOG_ITEM_SYS_ID,
    ERROR_CATALOG_AUTH_FAILED,
    ERROR_CATALOG_ACCESS_DENIED,
    ERROR_CATALOG_INVALID_REQUEST,
    ERROR_CATALOG_ITEM_NOT_FOUND,
    ERROR_CATALOG_ORDER_FAILED,
    ERROR_USER_NOT_FOUND,
    ERROR_USER_AMBIGUOUS,
    ERROR_APPLICATION_NOT_FOUND,
    ERROR_APPLICATION_AMBIGUOUS,
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
    requested_for_sys_id: str,
) -> Dict[str, str]:
    """Build the variables dict for the access-request catalog item.

    `requested_for_sys_id` is set as `cat_requested_for` and the routing field
    is set to "someone_else" so the catalog form resolves the requester from the
    variable rather than from the authenticated session user (the OAuth service
    account).  This is the correct mechanism — ServiceNow's order_now endpoint
    does NOT honour a top-level `sysparm_requested_for` body field."""
    return {
        "what_can_we_help_you_with": "Access to Application",
        "request_type": request_type,
        "is_the_request_for_you_or_someone_else": "someone_else",
        "cat_requested_for": requested_for_sys_id,
        "select_application": application_sys_id,
        "describe_access_level_needed_in_selected_system": access_level,
        "describe_your_request": justification,
        "business_justification": justification,
        "vs_cc_multi_select_summary": "",
        "cc_summary": "",
        "cc_set": "",
    }


_SYS_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _looks_like_sys_id(s: str) -> bool:
    return bool(_SYS_ID_RE.match(s))


async def _resolve_user(identifier: str) -> str:
    """Resolve sys_user by sys_id, email, or user_name. Returns sys_id or error string."""
    if _looks_like_sys_id(identifier):
        return identifier

    # Try email first
    url = f"{NWS_API_BASE}/api/now/table/sys_user?sysparm_query=email={identifier}&sysparm_fields=sys_id"
    data = await make_nws_request(url)
    results = (data or {}).get("result") or []

    if not results:
        # Fall back to user_name
        url = f"{NWS_API_BASE}/api/now/table/sys_user?sysparm_query=user_name={identifier}&sysparm_fields=sys_id"
        data = await make_nws_request(url)
        results = (data or {}).get("result") or []

    if not results:
        return ERROR_USER_NOT_FOUND.format(identifier=identifier)
    if len(results) > 1:
        sys_ids = ", ".join(r["sys_id"] for r in results)
        return ERROR_USER_AMBIGUOUS.format(identifier=identifier, sys_ids=sys_ids)
    return results[0]["sys_id"]


async def _get_catalog_item(catalog_item_sys_id: str) -> Optional[Dict[str, Any]]:
    """Fetch raw catalog item record (includes variables array). Returns None if not found.
    Internal helper — public callers use get_catalog_item_variables."""
    url = f"{NWS_API_BASE}/api/sn_sc/v1/servicecatalog/items/{catalog_item_sys_id}"
    data = await make_nws_request(url)
    if not data or not data.get("result"):
        return None
    return data["result"]


async def get_catalog_item_variables(catalog_item_sys_id: str) -> Dict[str, Any] | str:
    """Introspect a catalog item's variables.

    Returns a dict with the catalog item's name, sys_id, and a `variables` list,
    where each entry has at minimum: name, type, mandatory, reference (if applicable).
    Useful for discovering what variables a catalog item expects before calling
    order_catalog_item.
    """
    item = await _get_catalog_item(catalog_item_sys_id)
    if item is None:
        return ERROR_CATALOG_ITEM_NOT_FOUND.format(sys_id=catalog_item_sys_id)
    return {
        "sys_id": item.get("sys_id", catalog_item_sys_id),
        "name": item.get("name"),
        "variables": item.get("variables", []),
    }


def _find_select_application_reference(item: Dict[str, Any]) -> Optional[str]:
    """Return the reference table for the select_application variable, or None."""
    for var in item.get("variables", []):
        if var.get("name") == "select_application":
            return var.get("reference")
    return None


async def _resolve_application(identifier: str, catalog_item_sys_id: str) -> str:
    """Resolve an application identifier (sys_id or name) to a sys_id.
    Discovers the source reference table from the catalog item's variable schema,
    then queries that table by `name`.
    """
    if _looks_like_sys_id(identifier):
        return identifier

    item = await _get_catalog_item(catalog_item_sys_id)
    if item is None:
        return ERROR_APPLICATION_NOT_FOUND.format(identifier=identifier)

    ref_table = _find_select_application_reference(item)
    if not ref_table:
        return ERROR_APPLICATION_NOT_FOUND.format(identifier=identifier)

    url = (
        f"{NWS_API_BASE}/api/now/table/{ref_table}"
        f"?sysparm_query=name={identifier}&sysparm_fields=sys_id,name"
    )
    data = await make_nws_request(url)
    results = (data or {}).get("result") or []

    if not results:
        return ERROR_APPLICATION_NOT_FOUND.format(identifier=identifier)
    if len(results) > 1:
        candidates = ", ".join(f"{r.get('name', '?')} ({r['sys_id']})" for r in results)
        return ERROR_APPLICATION_AMBIGUOUS.format(identifier=identifier, candidates=candidates)
    return results[0]["sys_id"]


async def order_catalog_item(
    catalog_item_sys_id: str,
    variables: Dict[str, Any],
    requested_for_sys_id: str,
    quantity: int = 1,
) -> Dict[str, Any] | str:
    """Submit a Service Catalog order via POST /api/sn_sc/v1/servicecatalog/items/{sys_id}/order_now.

    Generic faithful API mirror — `variables` is passed through unchanged.
    `requested_for_sys_id` is accepted for API symmetry but NOT transmitted in
    the order_now body — ServiceNow's order_now endpoint doesn't accept it as a
    top-level parameter (it silently ignores it and defaults to the OAuth session
    user).  To set requested_for, the catalog item must have a `cat_requested_for`
    variable in its `variables` dict (see _build_access_request_variables).

    Returns the `result` from order_now (REQ details: sys_id, number, ...) on success,
    or an error string on failure.
    """
    url = f"{NWS_API_BASE}/api/sn_sc/v1/servicecatalog/items/{catalog_item_sys_id}/order_now"
    body: Dict[str, Any] = {
        "sysparm_quantity": str(quantity),
        "variables": variables,
        "sysparm_no_validation": "true",
    }
    return await _make_authenticated_request("POST", url, body)


async def _fetch_ritms_for_request(req_sys_id: str) -> list:
    """Get all sc_req_item records belonging to a parent sc_request."""
    url = (
        f"{NWS_API_BASE}/api/now/table/sc_req_item"
        f"?sysparm_query=request={req_sys_id}&sysparm_fields=sys_id,number"
    )
    data = await make_nws_request(url)
    return (data or {}).get("result") or []


# Derive stable prefixes by splitting on the first colon (template placeholder follows it).
# This way, renames in constants.py automatically propagate.
_ERROR_PREFIXES = tuple(
    c.split(":")[0]
    for c in (
        ERROR_USER_NOT_FOUND,
        ERROR_USER_AMBIGUOUS,
        ERROR_APPLICATION_NOT_FOUND,
        ERROR_APPLICATION_AMBIGUOUS,
    )
)


def _is_resolver_error(s: str) -> bool:
    """True if s is a known resolver error string (user/application).
    Does NOT cover catalog HTTP errors — those are detected with isinstance(x, str) at the call site."""
    return any(s.startswith(prefix) for prefix in _ERROR_PREFIXES)


async def create_access_request(
    application: str,
    access_level: str,
    justification: str,
    requested_for: str,
    request_type: str = "new_user",
    catalog_item_sys_id: str = ACCESS_REQUEST_CATALOG_ITEM_SYS_ID,
) -> Dict[str, Any] | str:
    """Submit an access request for an application.

    Args:
        application: Application name OR sys_id. Names are resolved via the
            catalog item's select_application variable schema.
        access_level: Free-text level (e.g., "Administrator", "Read-Only").
        justification: Free-text business justification.
        requested_for: User sys_id, email, or user_name.
        request_type: "new_user", "modify", or "remove" (default: "new_user").
        catalog_item_sys_id: Defaults to the Sweetgreen access-request catalog item.

    Returns:
        On success: {"req_number", "req_sys_id", "ritm_numbers", "ritm_sys_ids"}.
        On failure: an error string describing the failure.
    """
    user_sys_id = await _resolve_user(requested_for)
    if _is_resolver_error(user_sys_id):
        return user_sys_id

    application_sys_id = await _resolve_application(application, catalog_item_sys_id)
    if _is_resolver_error(application_sys_id):
        return application_sys_id

    variables = _build_access_request_variables(
        application_sys_id=application_sys_id,
        access_level=access_level,
        justification=justification,
        request_type=request_type,
        requested_for_sys_id=user_sys_id,
    )

    order_result = await order_catalog_item(
        catalog_item_sys_id=catalog_item_sys_id,
        variables=variables,
        requested_for_sys_id=user_sys_id,
    )
    if isinstance(order_result, str):
        return order_result

    req_sys_id = order_result.get("sys_id", "")
    req_number = order_result.get("number", "")

    ritms = await _fetch_ritms_for_request(req_sys_id) if req_sys_id else []

    return {
        "req_number": req_number,
        "req_sys_id": req_sys_id,
        "ritm_numbers": [r["number"] for r in ritms],
        "ritm_sys_ids": [r["sys_id"] for r in ritms],
    }
