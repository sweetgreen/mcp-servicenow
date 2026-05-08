"""Tests for tool allowlist configuration."""
import os
import pytest
from tool_allowlist import (
    get_enabled_tool_names,
    filter_tools,
    TOOL_GROUPS,
    PROFILES,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Ensure clean environment for each test."""
    monkeypatch.delenv("SERVICENOW_TOOL_PROFILE", raising=False)
    monkeypatch.delenv("SERVICENOW_ENABLED_GROUPS", raising=False)


class TestGetEnabledToolNames:
    def test_default_is_tickets(self):
        names = get_enabled_tool_names()
        # Should include incident tools
        assert "get_incident_details" in names
        assert "similar_incidents_for_text" in names
        # Should include request tools
        assert "get_request_item_details" in names
        # Should NOT include CMDB
        assert "find_cis_by_type" not in names
        # Should NOT include knowledge
        assert "get_knowledge_details" not in names

    def test_tickets_profile(self, monkeypatch):
        monkeypatch.setenv("SERVICENOW_TOOL_PROFILE", "tickets")
        names = get_enabled_tool_names()
        # Auth tools always included
        assert "nowtest" in names
        assert "now_test_oauth" in names
        # Incidents + requests + universal + intelligent
        assert "get_incident_details" in names
        assert "get_request_item_details" in names
        assert "intelligent_search" in names
        # No changes, CMDB, KB, SLAs
        assert "get_change_details" not in names
        assert "find_cis_by_type" not in names

    def test_itsm_profile(self, monkeypatch):
        monkeypatch.setenv("SERVICENOW_TOOL_PROFILE", "itsm")
        names = get_enabled_tool_names()
        # Should include changes and SLAs
        assert "get_change_details" in names
        assert "get_breaching_slas" in names
        # Should NOT include CMDB or KB
        assert "find_cis_by_type" not in names
        assert "get_knowledge_details" not in names

    def test_full_profile(self, monkeypatch):
        monkeypatch.setenv("SERVICENOW_TOOL_PROFILE", "full")
        names = get_enabled_tool_names()
        # Everything
        all_tools = set()
        for group_tools in TOOL_GROUPS.values():
            all_tools.update(group_tools)
        assert names == all_tools

    def test_custom_profile(self, monkeypatch):
        monkeypatch.setenv("SERVICENOW_TOOL_PROFILE", "custom")
        monkeypatch.setenv("SERVICENOW_ENABLED_GROUPS", "auth,incidents")
        names = get_enabled_tool_names()
        # Only auth + incidents
        assert "nowtest" in names
        assert "get_incident_details" in names
        assert "get_request_item_details" not in names
        assert "find_cis_by_type" not in names

    def test_custom_empty_falls_back(self, monkeypatch):
        monkeypatch.setenv("SERVICENOW_TOOL_PROFILE", "custom")
        monkeypatch.setenv("SERVICENOW_ENABLED_GROUPS", "")
        names = get_enabled_tool_names()
        # Falls back to tickets profile
        assert "get_incident_details" in names

    def test_unknown_profile_falls_back(self, monkeypatch):
        monkeypatch.setenv("SERVICENOW_TOOL_PROFILE", "nonexistent")
        names = get_enabled_tool_names()
        # Falls back to tickets profile
        assert "get_incident_details" in names
        assert "find_cis_by_type" not in names

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("SERVICENOW_TOOL_PROFILE", "ITSM")
        names = get_enabled_tool_names()
        assert "get_change_details" in names


class TestFilterTools:
    def test_filters_correctly(self, monkeypatch):
        monkeypatch.setenv("SERVICENOW_TOOL_PROFILE", "tickets")

        def allowed_tool():
            pass
        allowed_tool.__name__ = "get_incident_details"

        def blocked_tool():
            pass
        blocked_tool.__name__ = "find_cis_by_type"

        result = filter_tools([allowed_tool, blocked_tool])
        assert allowed_tool in result
        assert blocked_tool not in result

    def test_preserves_order(self, monkeypatch):
        monkeypatch.setenv("SERVICENOW_TOOL_PROFILE", "full")

        def tool_a():
            pass
        tool_a.__name__ = "nowtest"

        def tool_b():
            pass
        tool_b.__name__ = "get_incident_details"

        result = filter_tools([tool_a, tool_b])
        assert result == [tool_a, tool_b]


class TestProfileCompleteness:
    """Ensure all tool groups are reachable via at least one profile."""

    def test_full_profile_covers_all_groups(self):
        full_groups = set(PROFILES["full"])
        all_groups = set(TOOL_GROUPS.keys())
        assert full_groups == all_groups

    def test_no_empty_groups(self):
        for group_name, tools in TOOL_GROUPS.items():
            assert len(tools) > 0, f"Group {group_name} is empty"

    def test_no_duplicate_tools_across_groups(self):
        seen = {}
        for group_name, tools in TOOL_GROUPS.items():
            for tool_name in tools:
                if tool_name in seen:
                    pytest.fail(
                        f"Tool {tool_name} appears in both "
                        f"{seen[tool_name]} and {group_name}"
                    )
                seen[tool_name] = group_name
