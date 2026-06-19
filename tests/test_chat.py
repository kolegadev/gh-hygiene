"""Tests for the chat module — LLM conversation loop and safety gate."""

import json
from unittest.mock import patch, MagicMock, PropertyMock

from gh_hygiene.chat import ChatSession, SYSTEM_PROMPT
from gh_hygiene.tools import register_tool, is_destructive


def _make_mock_response(content=None, tool_calls=None):
    """Helper to create a mock DeepSeek API response."""
    message = MagicMock()
    message.content = content or ""
    message.tool_calls = tool_calls or []
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


class TestChatSessionInit:
    """Test ChatSession initialization."""

    def test_init_sets_system_prompt(self):
        """Session starts with the system prompt."""
        session = ChatSession("fake-api-key", MagicMock())
        assert len(session._messages) == 1
        assert session._messages[0]["role"] == "system"
        assert session._messages[0]["content"] == SYSTEM_PROMPT

    def test_init_creates_llm_client(self):
        """Session creates an OpenAI client pointing to DeepSeek."""
        with patch("gh_hygiene.chat.OpenAI") as mock_openai:
            session = ChatSession("test-key", MagicMock())
            mock_openai.assert_called_once_with(
                api_key="test-key",
                base_url="https://api.deepseek.com",
            )


class TestSendMessage:
    """Test message sending and response handling."""

    def test_simple_text_response(self):
        """LLM returns plain text — it's returned to the caller."""
        session = ChatSession("key", MagicMock())
        response = _make_mock_response(content="Hello! How can I help?")
        session._call_llm = MagicMock(return_value=response)

        result = session.send_message("hi")
        assert result == "Hello! How can I help?"
        assert len(session._messages) == 3  # system + user + assistant

    def test_tool_call_read_only(self):
        """LLM calls a read-only tool — it's executed and results fed back."""
        session = ChatSession("key", MagicMock())

        # Register a read-only test tool
        register_tool("list_repos", lambda **kw: [{"name": "repo1"}, {"name": "repo2"}])

        # Create a tool call response
        tc = MagicMock()
        tc.id = "call_123"
        tc.function.name = "list_repos"
        tc.function.arguments = json.dumps({"sort_by": "name"})

        response = _make_mock_response(
            content="Let me list your repos.",
            tool_calls=[tc],
        )

        # Second response: LLM processes results and returns text
        response2 = _make_mock_response(content="You have 2 repos: repo1, repo2.")

        session._call_llm = MagicMock(side_effect=[response, response2])

        result = session.send_message("list my repos")
        assert result == "You have 2 repos: repo1, repo2."

    def test_destructive_tool_dry_run(self):
        """Destructive tool called with dry_run=True — stores pending, returns preview."""
        session = ChatSession("key", MagicMock())

        archive_called = []

        def mock_archive(older_than_days, dry_run):
            archive_called.append({"older_than_days": older_than_days, "dry_run": dry_run})
            return [{"name": "old-repo"}]

        register_tool("archive_repos", mock_archive)

        tc = MagicMock()
        tc.id = "call_456"
        tc.function.name = "archive_repos"
        tc.function.arguments = json.dumps({"older_than_days": 365, "dry_run": True})

        response = _make_mock_response(
            content="Here's a preview of repos to archive.",
            tool_calls=[tc],
        )

        session._call_llm = MagicMock(return_value=response)

        result = session.send_message("archive old repos")
        assert "Here's a preview" in result
        assert len(session._pending_destructive) == 1
        assert session._pending_destructive["call_456"]["tool_name"] == "archive_repos"
        assert archive_called[0]["dry_run"] is True


class TestConfirmReject:
    """Test confirmation and rejection of pending destructive actions."""

    def test_confirm_pending(self):
        """Confirming executes the pending action with dry_run=False."""
        session = ChatSession("key", MagicMock())

        executed = []

        def mock_archive(older_than_days, dry_run):
            executed.append({"older_than_days": older_than_days, "dry_run": dry_run})
            return [{"name": "archived-repo"}]

        register_tool("archive_repos", mock_archive)

        # Set up pending action
        session._pending_destructive["call_789"] = {
            "tool_name": "archive_repos",
            "args": {"older_than_days": 365, "dry_run": True},
        }

        # Mock LLM's response after execution
        response = _make_mock_response(content="Done! 1 repo archived.")
        session._call_llm = MagicMock(return_value=response)

        result = session.confirm_pending()
        assert len(session._pending_destructive) == 0
        assert executed[0]["dry_run"] is False
        assert "Done!" in result

    def test_reject_pending(self):
        """Rejecting tells the LLM the action was declined."""
        session = ChatSession("key", MagicMock())

        session._pending_destructive["call_abc"] = {
            "tool_name": "archive_repos",
            "args": {"older_than_days": 365, "dry_run": True},
        }

        response = _make_mock_response(content="No problem! I won't archive anything.")
        session._call_llm = MagicMock(return_value=response)

        result = session.reject_pending()
        assert len(session._pending_destructive) == 0
        assert "No problem" in result

    def test_confirm_with_no_pending(self):
        """Confirming when nothing is pending returns a helpful message."""
        session = ChatSession("key", MagicMock())
        result = session.confirm_pending()
        assert "No pending actions" in result

    def test_reject_with_no_pending(self):
        """Rejecting when nothing is pending returns a helpful message."""
        session = ChatSession("key", MagicMock())
        result = session.reject_pending()
        assert "No pending actions" in result


class TestTruncateResult:
    """Test result truncation for LLM context management."""

    def test_truncate_long_list(self):
        """Lists longer than max_items are truncated."""
        session = ChatSession("key", MagicMock())
        long_list = [{"item": i} for i in range(100)]
        result = session._truncate_result(long_list, max_items=50)
        assert len(result) == 51  # 50 items + 1 truncation marker
        assert result[-1]["truncated"] is True

    def test_no_truncate_short_list(self):
        """Short lists are not truncated."""
        session = ChatSession("key", MagicMock())
        short_list = [{"item": i} for i in range(10)]
        result = session._truncate_result(short_list, max_items=50)
        assert len(result) == 10
        assert result == short_list

    def test_truncate_dict_with_repos(self):
        """Dicts with 'repos' key are truncated."""
        session = ChatSession("key", MagicMock())
        data = {"repos": {f"repo{i}": {} for i in range(100)}}
        result = session._truncate_result(data, max_items=50)
        assert len(result["repos"]) == 50
        assert result["truncated"] is True
        assert result["total_repos"] == 100

    def test_non_truncatable_passes_through(self):
        """Non-list, non-repos-dict passes through unchanged."""
        session = ChatSession("key", MagicMock())
        data = {"status": "ok", "count": 5}
        result = session._truncate_result(data)
        assert result == data


class TestRunOneShot:
    """Test the one-shot mode."""

    def test_one_shot_forces_dry_run(self):
        """One-shot mode forces dry_run=True on destructive tools."""
        from gh_hygiene.chat import run_one_shot

        called_with = []

        def mock_archive(older_than_days, dry_run):
            called_with.append({"older_than_days": older_than_days, "dry_run": dry_run})
            return []

        register_tool("archive_repos", mock_archive)

        tc = MagicMock()
        tc.id = "call_xyz"
        tc.function.name = "archive_repos"
        tc.function.arguments = json.dumps({"older_than_days": 365, "dry_run": False})

        response = _make_mock_response(
            content="Preview: these repos would be archived.",
            tool_calls=[tc],
        )

        with patch("gh_hygiene.chat.OpenAI"):
            with patch.object(ChatSession, "_call_llm", return_value=response):
                with patch("gh_hygiene.chat.print_markdown"):
                    run_one_shot("fake-key", MagicMock(), "archive old repos")

        # Should have been called with dry_run=True even though LLM tried False
        assert called_with[0]["dry_run"] is True
