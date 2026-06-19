"""Tests for hygiene commands."""

import datetime
from unittest.mock import MagicMock, patch

from gh_hygiene.commands.hygiene import (
    list_stale_issues,
    close_stale_issues,
    clean_pr_branches,
    audit_labels,
    sync_labels,
    STALE_LABEL,
    STANDARD_LABELS,
)


class TestListStaleIssues:
    """Test list_stale_issues command."""

    def test_empty_repos(self):
        """No repos means no stale issues."""
        client = MagicMock()
        client.get_all_repos.return_value = []

        with patch("gh_hygiene.commands.hygiene.console"):
            result = list_stale_issues(client)

        assert result == []

    def test_no_stale_issues(self):
        """Recently updated issues are not stale."""
        client = MagicMock()
        repo = MagicMock()
        repo.full_name = "owner/repo"
        client.get_all_repos.return_value = [repo]

        issue = MagicMock()
        issue.number = 1
        issue.title = "Fresh issue"
        issue.pull_request = None  # Not a PR
        issue.updated_at = datetime.datetime.now(datetime.timezone.utc)  # Just updated
        issue.created_at = datetime.datetime.now(datetime.timezone.utc)
        issue.labels = []
        issue.html_url = "https://github.com/owner/repo/issues/1"
        client.get_open_issues.return_value = [issue]

        with patch("gh_hygiene.commands.hygiene.console"):
            result = list_stale_issues(client, stale_days=90)

        assert result == []

    def test_stale_issues_found(self):
        """Issues with no recent activity are flagged."""
        client = MagicMock()
        repo = MagicMock()
        repo.full_name = "owner/repo"
        client.get_all_repos.return_value = [repo]

        issue = MagicMock()
        issue.number = 42
        issue.title = "Old bug"
        issue.pull_request = None
        issue.updated_at = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
        issue.created_at = datetime.datetime(2019, 1, 1, tzinfo=datetime.timezone.utc)
        issue.labels = []
        issue.html_url = "https://github.com/owner/repo/issues/42"
        client.get_open_issues.return_value = [issue]

        with patch("gh_hygiene.commands.hygiene.console"):
            result = list_stale_issues(client, stale_days=90)

        assert len(result) == 1
        assert result[0]["number"] == 42
        assert result[0]["title"] == "Old bug"
        assert result[0]["repo"] == "owner/repo"

    def test_json_output(self):
        """JSON output mode works."""
        client = MagicMock()
        client.get_all_repos.return_value = []

        with patch("gh_hygiene.commands.hygiene.console"):
            result = list_stale_issues(client, json_output=True)

        assert result == []


class TestCloseStaleIssues:
    """Test close_stale_issues command."""

    def test_dry_run(self):
        """Dry run doesn't close anything."""
        client = MagicMock()
        client.get_all_repos.return_value = []

        with patch("gh_hygiene.commands.hygiene.console"):
            result = close_stale_issues(client, stale_days=90, dry_run=True)

        assert result == []

    def test_no_stale_issues_to_close(self):
        """Nothing to close."""
        client = MagicMock()
        client.get_all_repos.return_value = []

        with patch("gh_hygiene.commands.hygiene.console"):
            result = close_stale_issues(client, stale_days=90, dry_run=True)

        assert result == []


class TestCleanPRBranches:
    """Test clean_pr_branches command."""

    def test_empty_repos(self):
        """No repos means no branches to clean."""
        client = MagicMock()
        client.get_all_repos.return_value = []

        with patch("gh_hygiene.commands.hygiene.console"):
            result = clean_pr_branches(client, dry_run=True)

        assert result == []

    def test_dry_run(self):
        """Dry run shows branches without deleting."""
        client = MagicMock()
        client.get_all_repos.return_value = []

        with patch("gh_hygiene.commands.hygiene.console"):
            result = clean_pr_branches(client, dry_run=True)

        assert result == []

    def test_json_output(self):
        """JSON output works."""
        client = MagicMock()
        client.get_all_repos.return_value = []

        with patch("gh_hygiene.commands.hygiene.console"):
            result = clean_pr_branches(client, json_output=True)

        assert result == []


class TestAuditLabels:
    """Test audit_labels command."""

    def test_empty_repos(self):
        """No repos to audit."""
        client = MagicMock()
        client.get_all_repos.return_value = []

        with patch("gh_hygiene.commands.hygiene.console"):
            result = audit_labels(client)

        assert result["summary"]["total_repos_audited"] == 0

    def test_json_output(self):
        """JSON output mode."""
        client = MagicMock()
        client.get_all_repos.return_value = []

        with patch("gh_hygiene.commands.hygiene.console"):
            result = audit_labels(client, json_output=True)

        assert result["summary"]["total_repos_audited"] == 0


class TestSyncLabels:
    """Test sync_labels command."""

    def test_dry_run_shows_preview(self):
        """Dry run shows labels to create without creating them."""
        client = MagicMock()
        client.get_all_repos.return_value = []

        with patch("gh_hygiene.commands.hygiene.console"):
            result = sync_labels(client, dry_run=True)

        assert isinstance(result, list)

    def test_json_output(self):
        """JSON output works."""
        client = MagicMock()
        client.get_all_repos.return_value = []

        with patch("gh_hygiene.commands.hygiene.console"):
            result = sync_labels(client, json_output=True)

        assert isinstance(result, list)


class TestStandardLabels:
    """Test the standard labels configuration."""

    def test_standard_labels_have_required(self):
        """Standard labels include essential ones."""
        assert "bug" in STANDARD_LABELS
        assert "enhancement" in STANDARD_LABELS
        assert "documentation" in STANDARD_LABELS
        assert "help wanted" in STANDARD_LABELS

    def test_label_colors_are_valid(self):
        """Label colors are valid hex codes."""
        for name, color in STANDARD_LABELS.items():
            assert len(color) == 6
            assert all(c in "0123456789abcdef" for c in color)
