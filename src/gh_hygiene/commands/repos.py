"""Repo commands: list, audit, archive, prune."""

from __future__ import annotations

import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from github.Repository import Repository

from ..client import GitHubClient
from ..display import (
    console,
    repo_table,
    print_success,
    print_warning,
    print_error,
    print_info,
    confirm_action,
    progress_bar,
)


def _repo_to_dict(repo: Repository) -> dict[str, Any]:
    """Convert a PyGithub Repository to a plain dict — fast, no extra API calls."""
    return {
        "name": repo.full_name,
        "visibility": "private" if repo.private else "public",
        "default_branch": repo.default_branch,
        "last_push": str(repo.pushed_at.date()) if repo.pushed_at else "never",
        "archived": repo.archived,
        "language": repo.language or "",
        "description": repo.description or "",
        "has_license": repo.license is not None,
        "stars": repo.stargazers_count,
        "forks": repo.forks_count,
        "open_issues": repo.open_issues_count,
        "created_at": str(repo.created_at.date()),
        "updated_at": str(repo.updated_at.date()) if repo.updated_at else "never",
    }


def _repo_to_dict_detailed(repo: Repository) -> dict[str, Any]:
    """Full repo details including topics and README status (extra API calls)."""
    d = _repo_to_dict(repo)
    d["topics"] = _get_topics(repo)
    d["has_readme"] = _has_readme(repo)
    return d


def _get_topics(repo: Repository) -> list[str]:
    """Get repo topics. Returns empty list on failure."""
    try:
        return repo.get_topics()
    except Exception:
        return []


def _has_readme(repo: Repository) -> bool:
    """Check if repo has a README file."""
    try:
        repo.get_readme()
        return True
    except Exception:
        return False


def list_repos(
    client: GitHubClient,
    sort_by: str = "name",
    filter_visibility: str = "all",
    filter_archived: Optional[bool] = None,
    json_output: bool = False,
) -> list[dict[str, Any]]:
    """List all repos for the authenticated user."""
    print_info("Fetching repos...")

    # Collect all repos (fast — paginated by PyGithub)
    all_repos = list(client.get_all_repos())
    total = len(all_repos)

    # Convert to dicts in parallel threads
    repos = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_repo_to_dict, r): r for r in all_repos}
        with progress_bar(total, "Processing repos") as pb:
            task = pb.add_task("", total=total)
            for future in as_completed(futures):
                d = future.result()
                # Apply filters
                if filter_visibility != "all" and d["visibility"] != filter_visibility:
                    pb.advance(task)
                    continue
                if filter_archived is not None and d["archived"] != filter_archived:
                    pb.advance(task)
                    continue
                repos.append(d)
                pb.advance(task)

    # Sort
    sort_key_map = {
        "name": lambda r: r["name"].lower(),
        "pushed": lambda r: r["last_push"],
        "created": lambda r: r["created_at"],
        "stars": lambda r: -r["stars"],
    }
    repos.sort(key=sort_key_map.get(sort_by, lambda r: r["name"].lower()))

    if json_output:
        from ..display import to_json
        console.print(to_json(repos))
    else:
        table = repo_table(repos, title=f"Repositories ({len(repos)})")
        console.print(table)
        console.print(f"\n[dim]Total: {len(repos)} repos[/]")

    return repos


def audit_repos(
    client: GitHubClient,
    repo_filter: str = "all",
    json_output: bool = False,
) -> dict[str, Any]:
    """Audit repos for common hygiene issues."""
    print_info("Auditing repositories...")

    repos = list(client.get_all_repos())
    if repo_filter != "all":
        repos = [r for r in repos if repo_filter.lower() in r.full_name.lower()]

    issues = {
        "critical": [],
        "warnings": [],
        "ok": [],
        "stats": {"total": len(repos), "critical_count": 0, "warning_count": 0, "ok_count": 0},
    }

    with progress_bar(len(repos), "Auditing") as pb:
        task = pb.add_task("", total=len(repos))
        for repo in repos:
            d = _repo_to_dict_detailed(repo)
            repo_problems = []

            # Critical issues
            if d["archived"]:
                repo_problems.append({"severity": "ok", "issue": "archived", "detail": "Already archived"})
            if not d["description"]:
                repo_problems.append({"severity": "warning", "issue": "No description", "detail": "Add a repo description"})
            if not d.get("has_readme", True) and not d["archived"]:
                repo_problems.append({"severity": "warning", "issue": "No README", "detail": "Add a README.md"})
            if d["default_branch"] and d["default_branch"] != "main" and not d["archived"]:
                repo_problems.append({"severity": "warning", "issue": f"Default branch: {d['default_branch']}", "detail": "Consider renaming to 'main'"})
            if not d["has_license"] and not d["archived"]:
                repo_problems.append({"severity": "warning", "issue": "No license", "detail": "Add a LICENSE file"})
            if not d.get("topics") and not d["archived"]:
                repo_problems.append({"severity": "info", "issue": "No topics", "detail": "Add topics for discoverability"})

            # Inactivity check
            if d["last_push"] != "never":
                try:
                    last_push = datetime.date.fromisoformat(d["last_push"])
                    days_since = (datetime.date.today() - last_push).days
                    if days_since > 365 and not d["archived"]:
                        repo_problems.append({
                            "severity": "critical",
                            "issue": f"No activity in {days_since} days",
                            "detail": "Candidate for archival",
                        })
                except (ValueError, TypeError):
                    pass

            # Categorize
            has_critical = any(p["severity"] == "critical" for p in repo_problems)
            has_warning = any(p["severity"] == "warning" for p in repo_problems)

            if has_critical:
                issues["critical"].append({"repo": d["name"], "problems": repo_problems})
                issues["stats"]["critical_count"] += 1
            elif has_warning:
                issues["warnings"].append({"repo": d["name"], "problems": repo_problems})
                issues["stats"]["warning_count"] += 1
            else:
                issues["ok"].append({"repo": d["name"], "problems": repo_problems})
                issues["stats"]["ok_count"] += 1

            pb.advance(task)

    if json_output:
        from ..display import to_json
        console.print(to_json(issues))
    else:
        _print_audit_summary(issues)

    return issues


def _print_audit_summary(issues: dict) -> None:
    """Pretty-print audit results."""
    s = issues["stats"]
    console.print(f"\n[bold]Audit Results: {s['total']} repos[/]\n")

    if issues["critical"]:
        console.print(f"[bold red]Critical ({s['critical_count']} repos):[/]")
        for entry in issues["critical"]:
            console.print(f"  [cyan]{entry['repo']}[/]")
            for p in entry["problems"]:
                console.print(f"    [red]• {p['issue']}[/] [dim]— {p['detail']}[/]")

    if issues["warnings"]:
        console.print(f"\n[bold yellow]Warnings ({s['warning_count']} repos):[/]")
        for entry in issues["warnings"]:
            console.print(f"  [cyan]{entry['repo']}[/]")
            for p in entry["problems"]:
                console.print(f"    [yellow]• {p['issue']}[/] [dim]— {p['detail']}[/]")

    console.print(f"\n[bold green]Clean ({s['ok_count']} repos)[/]")
    console.print(f"\n[dim]Total: {s['total']} | Critical: {s['critical_count']} | Warnings: {s['warning_count']} | OK: {s['ok_count']}[/]")


def archive_repos(
    client: GitHubClient,
    older_than_days: int = 365,
    dry_run: bool = True,
    confirm: bool = False,
    json_output: bool = False,
) -> list[dict[str, Any]]:
    """Archive repos with no activity older than `older_than_days`."""
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=older_than_days)
    candidates = []

    print_info(f"Finding repos with no pushes since {cutoff.date()}...")

    for repo in client.get_all_repos():
        if repo.archived:
            continue
        if repo.pushed_at and repo.pushed_at < cutoff:
            candidates.append(repo)

    if not candidates:
        print_success("No repos found matching the criteria.")
        return []

    result = [_repo_to_dict(r) for r in candidates]

    if json_output:
        from ..display import to_json
        console.print(to_json(result))
        return result

    table = repo_table(result, title=f"Repos to archive ({len(candidates)})")
    console.print(table)

    if dry_run:
        console.print("\n[yellow]Dry run mode — no changes made.[/]")
        console.print(f"[dim]Run with --no-dry-run to archive these {len(candidates)} repos.[/]")
        return result

    if not confirm:
        confirm = confirm_action(
            f"Archive {len(candidates)} repos? This is reversible.", default=False
        )

    if not confirm:
        print_warning("Aborted.")
        return result

    archived = []
    with progress_bar(len(candidates), "Archiving repos") as pb:
        task = pb.add_task("", total=len(candidates))
        for repo in candidates:
            try:
                client.archive_repo(repo)
                archived.append(repo.full_name)
                print_success(f"Archived: {repo.full_name}")
            except Exception as e:
                print_error(f"Failed to archive {repo.full_name}: {e}")
            pb.advance(task)

    print_success(f"Archived {len(archived)} repos.")
    return result
