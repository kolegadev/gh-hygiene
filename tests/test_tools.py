"""Tests for tool definitions and function registry."""

import pytest
from gh_hygiene.tools import (
    TOOL_DEFINITIONS,
    register_tool,
    get_tool_function,
    is_destructive,
    is_read_only,
    DESTRUCTIVE_TOOLS,
)


class TestToolDefinitions:
    """Test tool definition schemas."""

    def test_all_tools_have_required_fields(self):
        """Every tool definition has type, function name, and parameters."""
        for tool in TOOL_DEFINITIONS:
            assert tool["type"] == "function"
            assert "name" in tool["function"]
            assert "description" in tool["function"]
            assert "parameters" in tool["function"]

    def test_destructive_tools_have_dry_run_param(self):
        """Destructive tools must have a dry_run parameter."""
        for tool in TOOL_DEFINITIONS:
            name = tool["function"]["name"]
            if name in DESTRUCTIVE_TOOLS:
                params = tool["function"]["parameters"]["properties"]
                assert "dry_run" in params, f"{name} missing dry_run parameter"
                assert params["dry_run"]["type"] == "boolean"

    def test_read_only_tools_do_not_require_dry_run(self):
        """Read-only tools don't necessarily need dry_run (but it's ok if they have it)."""
        read_only_tools = [t for t in TOOL_DEFINITIONS if t["function"]["name"] not in DESTRUCTIVE_TOOLS]
        for tool in read_only_tools:
            # Read-only tools should not be in destructive set
            assert tool["function"]["name"] not in DESTRUCTIVE_TOOLS

    def test_all_expected_tools_present(self):
        """Verify all expected tool names are defined."""
        names = {t["function"]["name"] for t in TOOL_DEFINITIONS}
        expected = {
            "list_repos", "audit_repos", "archive_repos",
            "audit_files", "clean_files",
            "list_stale_issues", "close_stale_issues",
            "clean_pr_branches", "audit_labels",
            "get_rate_limit",
        }
        missing = expected - names
        assert not missing, f"Missing tool definitions: {missing}"


class TestFunctionRegistry:
    """Test the tool function registry."""

    def test_register_and_get(self):
        """Registering a function makes it retrievable."""
        def my_func(x=1):
            return x * 2

        register_tool("test_tool", my_func)
        fn = get_tool_function("test_tool")
        assert fn is not None
        assert fn(x=5) == 10

    def test_get_unregistered_tool(self):
        """Getting an unregistered tool returns None."""
        fn = get_tool_function("nonexistent")
        assert fn is None

    def test_register_overwrites(self):
        """Registering the same name overwrites previous."""
        def first():
            return "first"

        def second():
            return "second"

        register_tool("overwrite_test", first)
        register_tool("overwrite_test", second)
        fn = get_tool_function("overwrite_test")
        assert fn() == "second"


class TestToolCategories:
    """Test destructive vs read-only classification."""

    def test_is_destructive(self):
        """Archive repos is destructive."""
        assert is_destructive("archive_repos") is True
        assert is_destructive("close_stale_issues") is True
        assert is_destructive("clean_pr_branches") is True
        assert is_destructive("clean_files") is True
        assert is_destructive("reorganize_files") is True

    def test_is_read_only(self):
        """Audit and list tools are read-only."""
        assert is_read_only("list_repos") is True
        assert is_read_only("audit_repos") is True
        assert is_read_only("audit_files") is True
        assert is_read_only("list_stale_issues") is True
        assert is_read_only("audit_labels") is True
        assert is_read_only("get_rate_limit") is True

    def test_unknown_tool_is_read_only(self):
        """Unknown tools are classified as read-only by default."""
        assert is_read_only("nonexistent_tool") is True
        assert is_destructive("nonexistent_tool") is False
