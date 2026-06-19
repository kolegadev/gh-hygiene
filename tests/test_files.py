"""Tests for file commands."""

from unittest.mock import MagicMock, patch

from gh_hygiene.commands.files import (
    _matches_pattern,
    _resolve_repos,
    audit_files,
    clean_files,
    reorganize_files,
    load_reorg_rules,
    CLUTTER_PATTERNS,
    LARGE_FILE_THRESHOLD,
)


class TestPatternMatching:
    """Test glob-style pattern matching."""

    def test_exact_match(self):
        assert _matches_pattern(".DS_Store", ".DS_Store") is True

    def test_wildcard_extension(self):
        assert _matches_pattern("test.pyc", "*.pyc") is True
        assert _matches_pattern("test.py", "*.pyc") is False

    def test_substring_match(self):
        assert _matches_pattern("npm-debug.log", "npm-debug.log*") is True
        assert _matches_pattern("npm-debug.log.123", "npm-debug.log*") is True

    def test_no_match(self):
        assert _matches_pattern("README.md", ".DS_Store") is False


class TestResolveRepos:
    """Test repo name resolution."""

    def test_all_repos(self):
        client = MagicMock()
        client.get_all_repos.return_value = [MagicMock(), MagicMock()]
        repos = _resolve_repos(client, "all")
        assert len(repos) == 2

    def test_full_name_match(self):
        client = MagicMock()
        repo = MagicMock()
        client.get_repo.return_value = repo
        repos = _resolve_repos(client, "owner/specific-repo")
        assert repos == [repo]

    def test_partial_match(self):
        client = MagicMock()
        repo1 = MagicMock()
        repo1.full_name = "owner/myproject-api"
        repo2 = MagicMock()
        repo2.full_name = "owner/myproject-web"
        repo3 = MagicMock()
        repo3.full_name = "owner/other"
        client.get_all_repos.return_value = [repo1, repo2, repo3]
        repos = _resolve_repos(client, "myproject")
        assert len(repos) == 2

    def test_not_found(self):
        client = MagicMock()
        client.get_all_repos.return_value = []
        repos = _resolve_repos(client, "nonexistent")
        assert repos == []


class TestAuditFiles:
    """Test audit_files command."""

    def test_empty_repos(self):
        client = MagicMock()
        client.get_all_repos.return_value = []
        with patch("gh_hygiene.commands.files.console"):
            result = audit_files(client, repo_name="all")
        assert result == []

    def test_repo_not_found(self):
        client = MagicMock()
        client.get_all_repos.return_value = []
        with patch("gh_hygiene.commands.files.console"):
            result = audit_files(client, repo_name="nonexistent")
        assert result == []
        assert len(result) == 0


class TestCleanFiles:
    """Test clean_files command."""

    def test_dry_run(self):
        """Dry run doesn't delete anything."""
        client = MagicMock()
        client.get_all_repos.return_value = []

        with patch("gh_hygiene.commands.files.console"):
            result = clean_files(client, repo_name="all", dry_run=True)

        assert result == []

    def test_json_output(self):
        """JSON output mode works."""
        client = MagicMock()
        client.get_all_repos.return_value = []

        with patch("gh_hygiene.commands.files.console"):
            result = clean_files(client, repo_name="all", dry_run=True, json_output=True)

        assert result == []


class TestReorganizeFiles:
    """Test reorganize_files command."""

    def test_repo_not_found(self):
        """Gracefully handles missing repos."""
        client = MagicMock()
        client.get_repo.return_value = None

        with patch("gh_hygiene.commands.files.console"):
            result = reorganize_files(client, "nonexistent/repo", [])

        assert result == []

    def test_dry_run_operations(self):
        """Dry run shows operations but doesn't execute."""
        client = MagicMock()
        repo = MagicMock()
        client.get_repo.return_value = repo

        operations = [
            {"action": "move", "from": "old/path.py", "to": "new/path.py", "commit_message": "chore: move"},
            {"action": "delete", "from": "junk.txt", "commit_message": "chore: delete"},
        ]

        with patch("gh_hygiene.commands.files.console"):
            result = reorganize_files(client, "owner/repo", operations, dry_run=True)

        assert len(result) == 2
        assert all(r["status"] == "dry_run" for r in result)

    def test_json_output(self):
        """JSON output mode skips console printing."""
        client = MagicMock()
        repo = MagicMock()
        client.get_repo.return_value = repo

        operations = [
            {"action": "move", "from": "a.py", "to": "b.py"},
        ]

        with patch("gh_hygiene.commands.files.console"):
            result = reorganize_files(client, "owner/repo", operations, json_output=True)

        assert len(result) == 1
        assert result[0]["action"] == "move"


class TestLoadReorgRules:
    """Test loading YAML reorg rules."""

    def test_load_rules(self, tmp_path):
        """Rules are loaded from a YAML file."""
        import yaml

        rules_file = tmp_path / "rules.yml"
        rules = {
            "operations": [
                {"action": "move", "repo": "my-repo", "from": "a.txt", "to": "b.txt"},
                {"action": "delete", "repo": "*", "path": ".DS_Store"},
            ]
        }
        rules_file.write_text(yaml.dump(rules))

        loaded = load_reorg_rules(str(rules_file))
        assert len(loaded) == 2
        assert loaded[0]["action"] == "move"
        assert loaded[1]["repo"] == "*"
