"""
Tool allowlist configuration for the ServiceNow MCP server.

Controls which MCP tools are exposed to Claude, enabling the "lockdown"
layer that restricts the MCP server to ticket-management operations only,
regardless of the user's ServiceNow role.

Configuration via environment variable:
    SERVICENOW_TOOL_PROFILE=tickets    (default - incidents, requests, comments)
    SERVICENOW_TOOL_PROFILE=itsm       (tickets + changes + SLAs)
    SERVICENOW_TOOL_PROFILE=full       (all tools including CMDB, KB, etc.)
    SERVICENOW_TOOL_PROFILE=custom     (use SERVICENOW_ENABLED_GROUPS env var)

For custom profiles:
    SERVICENOW_ENABLED_GROUPS=incidents,changes,requests
"""
import os
from typing import Callable, Sequence

# Tool group definitions: maps group name -> list of tool function names
TOOL_GROUPS: dict[str, list[str]] = {
    "auth": [
        "nowtest",
        "now_test_oauth",
        "now_auth_info",
        "nowtestauth",
        "nowtest_auth_input",
    ],
    "incidents": [
        "similar_incidents_for_text",
        "get_short_desc_for_incident",
        "similar_incidents_for_incident",
        "get_incident_details",
        "get_incidents_by_filter",
        "get_priority_incidents",
    ],
    "changes": [
        "similar_changes_for_text",
        "get_short_desc_for_change",
        "similar_changes_for_change",
        "get_change_details",
    ],
    "requests": [
        "similar_request_items_for_text",
        "get_short_desc_for_request_item",
        "similar_request_items_for_request_item",
        "get_request_item_details",
    ],
    "universal_requests": [
        "similar_universal_requests_for_text",
        "get_short_desc_for_universal_request",
        "similar_universal_requests_for_universal_request",
        "get_universal_request_details",
    ],
    "knowledge": [
        "similar_knowledge_for_text",
        "get_knowledge_details",
        "get_knowledge_by_category",
        "get_active_knowledge_articles",
    ],
    "private_tasks": [
        "similar_private_tasks_for_text",
        "get_short_desc_for_private_task",
        "similar_private_tasks_for_private_task",
        "get_private_task_details",
        "get_private_tasks_by_filter",
        "create_private_task",
        "update_private_task",
    ],
    "slas": [
        "similar_slas_for_text",
        "get_slas_for_task",
        "get_sla_details",
        "get_breaching_slas",
        "get_breached_slas",
        "get_slas_by_stage",
        "get_active_slas",
        "get_sla_performance_summary",
        "get_recent_breached_slas",
        "get_critical_sla_status",
    ],
    "cmdb": [
        "find_cis_by_type",
        "search_cis_by_attributes",
        "get_ci_details",
        "similar_cis_for_ci",
        "get_all_ci_types",
        "quick_ci_search",
    ],
    "intelligent_query": [
        "intelligent_search",
        "explain_servicenow_filters",
        "build_smart_servicenow_filter",
        "get_servicenow_filter_templates",
        "get_query_examples",
    ],
}

# Profile definitions: maps profile name -> list of enabled group names
PROFILES: dict[str, list[str]] = {
    "tickets": [
        "auth",
        "incidents",
        "requests",
        "universal_requests",
        "intelligent_query",
    ],
    "itsm": [
        "auth",
        "incidents",
        "changes",
        "requests",
        "universal_requests",
        "slas",
        "intelligent_query",
    ],
    "full": list(TOOL_GROUPS.keys()),
}


def get_enabled_tool_names() -> set[str]:
    """Get the set of tool function names that should be registered.

    Reads SERVICENOW_TOOL_PROFILE and optionally SERVICENOW_ENABLED_GROUPS
    from environment variables.

    Returns:
        Set of tool function names that are allowed.
    """
    profile = os.getenv("SERVICENOW_TOOL_PROFILE", "tickets").lower().strip()

    if profile == "custom":
        groups_str = os.getenv("SERVICENOW_ENABLED_GROUPS", "")
        group_names = [g.strip() for g in groups_str.split(",") if g.strip()]
        if not group_names:
            # Fallback to tickets profile if no groups specified
            group_names = PROFILES["tickets"]
    elif profile in PROFILES:
        group_names = PROFILES[profile]
    else:
        # Unknown profile — fall back to tickets for safety
        group_names = PROFILES["tickets"]

    enabled: set[str] = set()
    for group_name in group_names:
        if group_name in TOOL_GROUPS:
            enabled.update(TOOL_GROUPS[group_name])

    return enabled


def filter_tools(tools: Sequence[Callable]) -> list[Callable]:
    """Filter a list of tool functions based on the active allowlist.

    Args:
        tools: Full list of tool functions to potentially register.

    Returns:
        Filtered list containing only allowed tools.
    """
    enabled = get_enabled_tool_names()
    return [tool for tool in tools if tool.__name__ in enabled]
