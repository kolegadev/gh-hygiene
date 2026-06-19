"""Tests for repo commands."""

import datetime
from unittest.mock import MagicMock, patch

from gh_hygiene.commands.repos import (
    _repo_to_dict,
    _has_readme,
    list_repos,
    audit_repos,
    archive_repos,
)


class TestRepoToDict:
    """Test repo-to-dict conversion."""

    def test_basic_conversion(self):
        """Key fields are extracted correctly."""
        repo = MagicMock()
        repo.full_name = "owner/test-repo"
        repo.private = False
        repo.default_branch = "main"
        repo.pushed_at = datetime.datetime(2024, 1, 15)
        repo.archived = False
        repo.language = "Python"
        repo.description = "A test repo"
        repo.stargazers_count = 5
        repo.forks_count = 2
        repo.open_issues_count = 3
        repo.created_at = datetime.datetime(2023, 6, 1)
        repo.updated_at = datetime.datetime(2024, 3, 1)
        repo.get_topics.return_value = ["python", "cli"]

        with patch("gh_hygiene.commands.repos._has_readme", return_value=True):
            repo.license = MagicMock()  # Has license
            result = _repo_to_dict(repo)

        assert result["name"] == "owner/test-repo"
        assert result["visibility"] == "public"
        assert result["default_branch"] == "main"
        assert result["last_push"] == "2024-01-15"
        assert result["archived"] is False
        assert result["language"] == "Python"
        assert result["has_readme"] is True
        assert result["has_license"] is True
        assert result["topics"] == ["python", "cli"]
        assert result["stars"] == 5
        assert result["forks"] == 2

    def test_private_repo(self):
        """Private repos are flagged correctly."""
        repo = MagicMock()
        repo.private = True
        repo.pushed_at = datetime.datetime(2024, 1, 1)
        repo.created_at = datetime.datetime(2023, 1, 1)
        repo.updated_at = datetime.datetime(2024, 1, 1)
        repo.get_topics.return_value = []
        repo.license = None

        with patch("gh_hygiene.commands.repos._has_readme", return_value=False):
            result = _repo_to_dict(repo)

        assert result["visibility"] == "private"

    def test_no_push(self):
        """Repos with no pushes show 'never'."""
        repo = MagicMock()
        repo.pushed_at = None  # No pushes
        repo.created_at = datetime.datetime(2023, 1, 1)
        repo.updated_at = datetime.datetime(2024, 1, 1)
        repo.get_topics.return_value = []
        repo.license = None

        with patch("gh_hygiene.commands.repos._has_readme", return_value=False):
            result = _repo_to_dict(repo)

        assert result["last_push"] == "never"


class TestListRepos:
    """Test list_repos command."""

    def test_list_basic(self):
        """Lists repos with default options."""
        client = MagicMock()
        repo = MagicMock()
        repo.full_name = "owner/repo1"
        repo.private = False
        repo.default_branch = "main"
        repo.pushed_at = datetime.datetime(2024, 6, 1)
        repo.archived = False
        repo.language = "Python"
        repo.description = ""
        repo.stargazers_count = 0
        repo.forks_count = 0
        repo.open_issues_count = 0
        repo.created_at = datetime.datetime(2024, 1, 1)
        repo.updated_at = datetime.datetime(2024, 6, 1)
        repo.get_topics.return_value = []
        repo.license = None

        client.get_all_repos.return_value = [repo]

        with patch("gh_hygiene.commands.repos._has_readme", return_value=False):
            with patch("gh_hygiene.commands.repos.console"):
                result = list_repos(client)

        assert len(result) == 1
        assert result[0]["name"] == "owner/repo1"

    def test_list_filter_visibility(self):
        """Filters by visibility."""
        client = MagicMock()

        public_repo = MagicMock()
        public_repo.private = False
        public_repo.archived = False
        public_repo.pushed_at = datetime.datetime(2024, 1, 1)
        public_repo.created_at = datetime.datetime(2023, 1, 1)
        public_repo.updated_at = datetime.datetime(2024, 1, 1)
        public_repo.full_name = "owner/public-repo"
        public_repo.default_branch = "main"
        public_repo.language = ""
        public_repo.description = ""
        public_repo.stargazers_count = 0
        public_repo.forks_count = 0
        public_repo.open_issues_count = 0
        public_repo.get_topics.return_value = []
        public_repo.license = None

        private_repo = MagicMock()
        private_repo.private = True
        private_repo.archived = False
        private_repo.pushed_at = datetime.datetime(2024, 1, 1)
        private_repo.created_at = datetime.datetime(2023, 1, 1)
        private_repo.updated_at = datetime.datetime(2024, 1, 1)
        private_repo.full_name = "owner/private-repo"
        private_repo.default_branch = "main"
        private_repo.language = ""
        private_repo.description = ""
        private_repo.stargazers_count = 0
        private_repo.forks_count = 0
        private_repo.open_issues_count = 0
        private_repo.get_topics.return_value = []
        private_repo.license = None

        client.get_all_repos.return_value = [public_repo, private_repo]

        with patch("gh_hygiene.commands.repos._has_readme", return_value=False):
            with patch("gh_hygiene.commands.repos.console"):
                with patch("gh_hygiene.commands.repos.progress_bar"):
                    result = list_repos(client, filter_visibility="private")

        assert len(result) == 1
        assert result[0]["name"] == "owner/private-repo"

    def test_list_json_output(self):
        """JSON output mode is accepted."""
        client = MagicMock()
        client.get_all_repos.return_value = []

        with patch("gh_hygiene.commands.repos.console"):
            result = list_repos(client, json_output=True)
        assert result == []


class TestAuditRepos:
    """Test audit_repos command."""

    def test_audit_empty(self):
        """Auditing with no repos returns empty results."""
        client = MagicMock()
        client.get_all_repos.return_value = []

        with patch("gh_hygiene.commands.repos.console"):
            result = audit_repos(client)

        assert result["stats"]["total"] == 0
        assert result["critical"] == []
        assert result["warnings"] == []

    def test_audit_stale_repo(self):
        """Repos inactive > 1 year are flagged as critical."""
        client = MagicMock()
        repo = MagicMock()
        repo.full_name = "owner/stale-repo"
        repo.private = False
        repo.default_branch = "main"
        repo.pushed_at = datetime.datetime(2020, 1, 1)  # 6+ years ago
        repo.archived = False
        repo.language = "Ruby"
        repo.description = "Some description"
        repo.stargazers_count = 0
        repo.forks_count = 0
        repo.open_issues_count = 0
        repo.created_at = datetime.datetime(2019, 1, 1)
        repo.updated_at = datetime.datetime(2020, 1, 1)
        repo.get_topics.return_value = ["ruby"]
        repo.license = MagicMock()  # Has license

        client.get_all_repos.return_value = [repo]

        with patch("gh_hygiene.commands.repos._has_readme", return_value=True):
            with patch("gh_hygiene.commands.repos.console"):
                result = audit_repos(client)

        assert result["stats"]["critical_count"] == 1
        assert result["stats"]["total"] == 1

    def test_audit_missing_readme(self):
        """Repos without README are flagged as warnings."""
        client = MagicMock()
        repo = MagicMock()
        repo.full_name = "owner/no-readme"
        repo.private = False
        repo.default_branch = "main"
        repo.pushed_at = datetime.datetime.now()  # Recently pushed
        repo.archived = False
        repo.language = "Python"
        repo.description = "A repo"
        repo.stargazers_count = 0
        repo.forks_count = 0
        repo.open_issues_count = 0
        repo.created_at = datetime.datetime(2024, 1, 1)
        repo.updated_at = datetime.datetime.now()
        repo.get_topics.return_value = ["python"]
        repo.license = MagicMock()

        client.get_all_repos.return_value = [repo]

        with patch("gh_hygiene.commands.repos._has_readme", return_value=False):
            with patch("gh_hygiene.commands.repos.console"):
                with patch("gh_hygiene.commands.repos.progress_bar"):
                    result = audit_repos(client)

        assert result["stats"]["total"] == 1
        assert result["stats"]["warning_count"] == 1, f"Got: critical={result['stats']['critical_count']}, warning={result['stats']['warning_count']}, ok={result['stats']['ok_count']}. Warnings: {result['warnings']}"

    def test_audit_json_output(self):
        """JSON output mode works."""
        client = MagicMock()
        client.get_all_repos.return_value = []

        with patch("gh_hygiene.commands.repos.console"):
            result = audit_repos(client, json_output=True)
        assert result["stats"]["total"] == 0


class TestArchiveRepos:
    """Test archive_repos command."""

    def test_archive_dry_run(self):
        """Dry run shows candidates but doesn't archive."""
        client = MagicMock()
        repo = MagicMock()
        repo.full_name = "owner/old-repo"
        repo.private = False
        repo.default_branch = "main"
        repo.pushed_at = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
        repo.archived = False
        repo.language = "JavaScript"
        repo.description = ""
        repo.stargazers_count = 0
        repo.forks_count = 0
        repo.open_issues_count = 0
        repo.created_at = datetime.datetime(2019, 1, 1)
        repo.updated_at = datetime.datetime(2020, 1, 1)
        repo.get_topics.return_value = []
        repo.license = None

        client.get_all_repos.return_value = [repo]

        with patch("gh_hygiene.commands.repos._has_readme", return_value=False):
            with patch("gh_hygiene.commands.repos.console"):
                result = archive_repos(client, older_than_days=365, dry_run=True)

        assert len(result) == 1
        assert result[0]["name"] == "owner/old-repo"
        client.archive_repo.assert_not_called()

    def test_archive_no_candidates(self):
        """Nothing found when no repos match."""
        client = MagicMock()
        repo = MagicMock()
        repo.archived = False
        repo.pushed_at = datetime.datetime.now(datetime.timezone.utc)  # Just now
        client.get_all_repos.return_value = [repo]

        with patch("gh_hygiene.commands.repos.console"):
            result = archive_repos(client, older_than_days=365)

        assert result == []

    def test_archive_skips_already_archived(self):
        """Already archived repos are not re-archived."""
        client = MagicMock()
        repo = MagicMock()
        repo.archived = True  # Already archived
        repo.pushed_at = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
        client.get_all_repos.return_value = [repo]

        with patch("gh_hygiene.commands.repos.console"):
            result = archive_repos(client, older_than_days=365)

        assert result == []
