"""
HTTP server entry point for the ServiceNow MCP Connector.

This module configures FastMCP to run as a Streamable HTTP server with
OAuth authentication, designed to be deployed as a Remote MCP Connector
for Claude Enterprise.

Usage:
    # Start HTTP server (for Claude Enterprise Remote Connector)
    python server.py

    # Start with custom port
    MCP_SERVER_PORT=9090 python server.py

    # Start in stdio mode (traditional Claude Desktop)
    python server.py --stdio

Environment variables:
    SERVICENOW_INSTANCE         ServiceNow instance URL
    SERVICENOW_OAUTH_CLIENT_ID  OAuth client ID registered in ServiceNow
    SERVICENOW_OAUTH_CLIENT_SECRET  OAuth client secret
    MCP_SERVER_BASE_URL         Public URL of this server (default: http://localhost:8080)
    MCP_SERVER_PORT             Port to listen on (default: 8080)
    MCP_SERVER_HOST             Host to bind to (default: 0.0.0.0)
    SERVICENOW_TOOL_PROFILE     Tool profile: tickets, itsm, full, custom (default: tickets)
    SERVICENOW_ENABLED_GROUPS   Comma-separated tool groups (when profile=custom)
    LOG_LEVEL                   Logging level (default: INFO)
"""
import argparse
import logging
import os
import sys

from starlette.applications import Starlette
from starlette.responses import RedirectResponse
from starlette.routing import Route

from mcp.server.fastmcp import FastMCP

from tool_allowlist import filter_tools

# Configure logging
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mcp-servicenow")


def get_all_tools() -> list:
    """Import and return the full list of available tool functions."""
    from Table_Tools.consolidated_tools import (
        similar_incidents_for_text, get_short_desc_for_incident,
        similar_incidents_for_incident, get_incident_details,
        get_incidents_by_filter, get_priority_incidents,
        similar_changes_for_text, get_short_desc_for_change,
        similar_changes_for_change, get_change_details,
        similar_request_items_for_text, get_short_desc_for_request_item,
        similar_request_items_for_request_item, get_request_item_details,
        similar_universal_requests_for_text, get_short_desc_for_universal_request,
        similar_universal_requests_for_universal_request, get_universal_request_details,
        similar_knowledge_for_text, get_knowledge_details,
        get_knowledge_by_category, get_active_knowledge_articles,
        similar_private_tasks_for_text, get_short_desc_for_private_task,
        similar_private_tasks_for_private_task, get_private_task_details,
        get_private_tasks_by_filter,
        similar_slas_for_text, get_slas_for_task, get_sla_details,
        get_breaching_slas, get_breached_slas, get_slas_by_stage,
        get_active_slas, get_sla_performance_summary,
        get_recent_breached_slas, get_critical_sla_status,
    )
    from Table_Tools.table_tools import nowtestauth, nowtest_auth_input
    from Table_Tools.vtb_task_tools import create_private_task, update_private_task
    from Table_Tools.cmdb_tools import (
        find_cis_by_type, search_cis_by_attributes, get_ci_details,
        similar_cis_for_ci, get_all_ci_types, quick_ci_search,
    )
    from utility_tools import nowtest, now_test_oauth, now_auth_info
    from Table_Tools.intelligent_query_tools import (
        intelligent_search, explain_servicenow_filters,
        build_smart_servicenow_filter, get_servicenow_filter_templates,
        get_query_examples,
    )

    return [
        # Auth
        nowtest, now_test_oauth, now_auth_info, nowtestauth, nowtest_auth_input,
        # Incidents
        similar_incidents_for_text, get_short_desc_for_incident,
        similar_incidents_for_incident, get_incident_details,
        get_incidents_by_filter, get_priority_incidents,
        # Changes
        similar_changes_for_text, get_short_desc_for_change,
        similar_changes_for_change, get_change_details,
        # Request Items
        similar_request_items_for_text, get_short_desc_for_request_item,
        similar_request_items_for_request_item, get_request_item_details,
        # Universal Requests
        similar_universal_requests_for_text, get_short_desc_for_universal_request,
        similar_universal_requests_for_universal_request, get_universal_request_details,
        # Knowledge
        similar_knowledge_for_text, get_knowledge_details,
        get_knowledge_by_category, get_active_knowledge_articles,
        # Private Tasks
        similar_private_tasks_for_text, get_short_desc_for_private_task,
        similar_private_tasks_for_private_task, get_private_task_details,
        get_private_tasks_by_filter, create_private_task, update_private_task,
        # SLAs
        similar_slas_for_text, get_slas_for_task, get_sla_details,
        get_breaching_slas, get_breached_slas, get_slas_by_stage,
        get_active_slas, get_sla_performance_summary,
        get_recent_breached_slas, get_critical_sla_status,
        # CMDB
        find_cis_by_type, search_cis_by_attributes, get_ci_details,
        similar_cis_for_ci, get_all_ci_types, quick_ci_search,
        # Intelligent Query
        intelligent_search, explain_servicenow_filters,
        build_smart_servicenow_filter, get_servicenow_filter_templates,
        get_query_examples,
    ]


def create_mcp_server() -> FastMCP:
    """Create and configure the FastMCP server with filtered tools."""
    mcp = FastMCP("sweetgreen-servicenow")

    all_tools = get_all_tools()
    allowed_tools = filter_tools(all_tools)

    profile = os.getenv("SERVICENOW_TOOL_PROFILE", "tickets")
    logger.info(
        "Tool profile: %s — registering %d of %d tools",
        profile,
        len(allowed_tools),
        len(all_tools),
    )

    for tool in allowed_tools:
        mcp.tool()(tool)

    return mcp


# ─── Health Check & Callback Routes ─────────────────────────────────────


async def health_check(request):
    """Health check endpoint for load balancers and monitoring."""
    from starlette.responses import JSONResponse

    return JSONResponse({
        "status": "healthy",
        "service": "mcp-servicenow",
        "version": "3.0.0",
        "transport": "streamable-http",
        "tool_profile": os.getenv("SERVICENOW_TOOL_PROFILE", "tickets"),
    })


async def servicenow_oauth_callback(request):
    """Handle the OAuth callback from ServiceNow.

    After the user authenticates via Okta SSO, ServiceNow redirects here
    with an authorization code. We translate it to an MCP auth code and
    redirect the user back to Claude.
    """
    code = request.query_params.get("code")
    state = request.query_params.get("state")

    if not code or not state:
        from starlette.responses import JSONResponse
        return JSONResponse(
            {"error": "Missing code or state parameter"},
            status_code=400,
        )

    try:
        from servicenow_oauth_provider import ServiceNowOAuthProvider

        # Get the global provider instance
        provider = _get_oauth_provider()
        redirect_url = await provider.handle_servicenow_callback(code, state)
        return RedirectResponse(url=redirect_url)
    except ValueError as e:
        from starlette.responses import JSONResponse
        return JSONResponse(
            {"error": str(e)},
            status_code=400,
        )
    except Exception as e:
        logger.error("OAuth callback error: %s", e, exc_info=True)
        from starlette.responses import JSONResponse
        return JSONResponse(
            {"error": "Internal server error during OAuth callback"},
            status_code=500,
        )


# Singleton OAuth provider
_oauth_provider_instance = None


def _get_oauth_provider():
    """Get or create the singleton OAuth provider."""
    global _oauth_provider_instance
    if _oauth_provider_instance is None:
        from servicenow_oauth_provider import ServiceNowOAuthProvider
        _oauth_provider_instance = ServiceNowOAuthProvider()
    return _oauth_provider_instance


# ─── Main Entry Point ────────────────────────────────────────────────────


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="mcp-servicenow-server",
        description="ServiceNow MCP Server — Claude Enterprise Connector",
    )
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="Run in stdio mode (for Claude Desktop local config)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MCP_SERVER_PORT", "8080")),
        help="Port to listen on (default: 8080)",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("MCP_SERVER_HOST", "0.0.0.0"),
        help="Host to bind to (default: 0.0.0.0)",
    )
    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()
    mcp = create_mcp_server()

    if args.stdio:
        logger.info("Starting in stdio mode")
        mcp.run(transport="stdio")
    else:
        logger.info(
            "Starting Streamable HTTP server on %s:%d",
            args.host,
            args.port,
        )

        # For Streamable HTTP, FastMCP.run() handles uvicorn internally.
        # We set the port/host via environment variables that uvicorn reads,
        # or we can use the streamable_http_app() method to get the ASGI app
        # and run it ourselves with additional routes.

        import uvicorn

        # Get the MCP ASGI app
        mcp_app = mcp.streamable_http_app()

        # Build a Starlette app that mounts the MCP app plus our custom routes
        app = Starlette(
            routes=[
                Route("/health", health_check, methods=["GET"]),
                Route("/oauth/servicenow/callback", servicenow_oauth_callback, methods=["GET"]),
            ],
        )

        # Mount the MCP app at the root
        app.mount("/mcp", mcp_app)

        logger.info("Routes: /health, /oauth/servicenow/callback, /mcp/*")

        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level=log_level.lower(),
        )


if __name__ == "__main__":
    main()
