"""GitHub API client wrapper.

Thin wrapper around PyGithub that handles:
- Pagination (critical for 120+ repos, forces per_page=100)
- Rate-limit detection and backoff
- Convenience methods for common operations
"""

from __future__ import annotations

import time
from typing import Iterator, Optional

from github import Github, GithubException, RateLimitExceededException
from github.Repository import Repository
from github.Issue import Issue
from github.PullRequest import PullRequest
from github.Label import Label
from github.GithubObject import NotSet
from github.PaginatedList import PaginatedList


class GitHubClient:
    """Wrapper around PyGithub with rate-limit handling, pagination, and caching."""

    def __init__(self, token: str):
        self._gh = Github(token, per_page=100)
        self._user = None
        self._repo_cache: Optional[list[Repository]] = None
        self._repo_dicts_cache: Optional[list[dict]] = None

    @property
    def user(self):
        """Lazy-loaded authenticated user."""
        if self._user is None:
            self._user = self._gh.get_user()
        return self._user

    def get_all_repos(self) -> Iterator[Repository]:
        """Yield all repos for the authenticated user, handling pagination."""
        repos = self.user.get_repos()
        yield from self._paginate(repos)

    def get_all_repos_cached(self) -> list[Repository]:
        """Return all repos, cached in memory. Refreshes on first call."""
        if self._repo_cache is None:
            self._repo_cache = list(self.get_all_repos())
        return self._repo_cache

    def get_repo_dicts_cached(self) -> list[dict]:
        """Return repo dicts (fast, no extra API calls), cached."""
        if self._repo_dicts_cache is None:
            from .commands.repos import _repo_to_dict
            repos = self.get_all_repos_cached()
            self._repo_dicts_cache = [_repo_to_dict(r) for r in repos]
        return self._repo_dicts_cache

    def invalidate_cache(self):
        """Clear the repo cache (call after mutations)."""
        self._repo_cache = None
        self._repo_dicts_cache = None

    def get_repo(self, name: str) -> Optional[Repository]:
        """Get a single repo by full name (e.g. 'owner/repo')."""
        try:
            return self._call_with_retry(lambda: self._gh.get_repo(name))
        except GithubException as e:
            if e.status == 404:
                return None
            raise

    def get_open_issues(self, repo: Repository, since_days: int = 0) -> Iterator[Issue]:
        """Yield all open issues for a repo, optionally filtered by days since update."""
        since = NotSet
        if since_days > 0:
            import datetime
            since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=since_days)
        issues = repo.get_issues(state="open", since=since)
        for issue in self._paginate(issues):
            if issue.pull_request is None:
                yield issue

    def get_open_pulls(self, repo: Repository) -> Iterator[PullRequest]:
        """Yield all open pull requests for a repo."""
        yield from self._paginate(repo.get_pulls(state="open"))

    def get_merged_pulls(self, repo: Repository) -> Iterator[PullRequest]:
        """Yield all merged pull requests for a repo."""
        for pr in self._paginate(repo.get_pulls(state="closed")):
            if pr.merged:
                yield pr

    def get_labels(self, repo: Repository) -> Iterator[Label]:
        """Yield all labels for a repo."""
        yield from self._paginate(repo.get_labels())

    def archive_repo(self, repo: Repository) -> bool:
        """Archive a repo. Returns True on success."""
        return self._call_with_retry(lambda: repo.edit(archived=True))

    def delete_repo(self, repo: Repository) -> bool:
        """Delete a repo. Returns True on success."""
        return self._call_with_retry(lambda: repo.delete())

    def get_rate_limit(self) -> dict:
        """Return current rate limit info."""
        rl = self._gh.get_rate_limit()
        return {
            "core_remaining": rl.core.remaining,
            "core_limit": rl.core.limit,
            "core_reset": str(rl.core.reset),
            "search_remaining": rl.search.remaining,
            "search_limit": rl.search.limit,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _paginate(self, paginated_list: PaginatedList):
        """Yield all items from a PyGithub PaginatedList, with rate-limit retry.

        PyGithub handles pagination internally — this wrapper just adds
        retry logic for each page fetch.
        """
        # PyGithub PaginatedList uses __iter__ which fetches pages as needed.
        # We wrap the underlying _grow() call with retry logic.
        it = iter(paginated_list)
        while True:
            try:
                item = self._call_with_retry(lambda: next(it))
                yield item
            except StopIteration:
                break
            except GithubException:
                break

    def _call_with_retry(self, fn, max_retries: int = 3):
        """Call a function with rate-limit retry logic."""
        for attempt in range(max_retries):
            try:
                return fn()
            except RateLimitExceededException:
                if attempt == max_retries - 1:
                    raise
                rl = self._gh.get_rate_limit()
                wait = max((rl.core.reset - time.time()), 0) + 1
                time.sleep(wait)
            except GithubException as e:
                if e.status == 403 and "rate limit" in str(e).lower():
                    if attempt == max_retries - 1:
                        raise
                    rl = self._gh.get_rate_limit()
                    wait = max((rl.core.reset - time.time()), 0) + 1
                    time.sleep(wait)
                else:
                    raise
        return None
