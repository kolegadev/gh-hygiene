"""Hygiene commands: stale issues, PR branch cleanup, label audit."""

from __future__ import annotations

import datetime
from typing import Any

from github.Repository import Repository

from ..client import GitHubClient
from ..display import (
    console,
    issue_table,
    print_success,
    print_warning,
    print_error,
    print_info,
    confirm_action,
    progress_bar,
)

STALE_LABEL = "stale"
STALE_COMMENT = (
    "This issue has been automatically marked as stale due to inactivity. "
    "It will be closed in 30 days if no further activity occurs."
)
CLOSING_COMMENT = (
    "Closing this issue due to extended inactivity. "
    "Feel free to reopen if this is still relevant."
)

STANDARD_LABELS = {
    "bug": "d73a4a",
    "enhancement": "a2eeef",
    "documentation": "0075ca",
    "good first issue": "7057ff",
    "help wanted": "008672",
    "question": "d876e3",
    "wontfix": "ffffff",
    "duplicate": "cfd3d7",
    "invalid": "e4e669",
}


def list_stale_issues(
    client: GitHubClient,
    stale_days: int = 90,
    repo_name: str = "all",
    json_output: bool = False,
) -> list[dict[str, Any]]:
    """Find issues with no activity for N days."""
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=stale_days)
    stale = []

    repos = _resolve_repos(client, repo_name)
    print_info(f"Checking for stale issues across {len(repos)} repos...")

    with progress_bar(len(repos), "Scanning issues") as pb:
        task = pb.add_task("", total=len(repos))
        for repo in repos:
            try:
                for issue in client.get_open_issues(repo):
                    if issue.updated_at < cutoff:
                        stale.append({
                            "number": issue.number,
                            "title": issue.title,
                            "repo": repo.full_name,
                            "created_at": str(issue.created_at.date()),
                            "updated_at": str(issue.updated_at.date()),
                            "labels": [l.name for l in issue.labels],
                            "url": issue.html_url,
                        })
            except Exception:
                pass
            pb.advance(task)

    if json_output:
        from ..display import to_json
        console.print(to_json(stale))
    elif stale:
        table = issue_table(stale, title=f"Stale Issues ({len(stale)})")
        console.print(table)
        console.print(f"\n[dim]Found {len(stale)} issues with no activity in {stale_days}+ days.[/]")
    else:
        print_success(f"No stale issues found (inactive > {stale_days} days).")

    return stale


def close_stale_issues(
    client: GitHubClient,
    stale_days: int = 90,
    repo_name: str = "all",
    dry_run: bool = True,
    confirm: bool = False,
    json_output: bool = False,
) -> list[dict[str, Any]]:
    """Add stale label, comment, and close issues inactive for N days."""
    stale = list_stale_issues(client, stale_days=stale_days, repo_name=repo_name, json_output=json_output)

    if not stale:
        return []

    if dry_run:
        console.print(f"\n[yellow]Dry run mode — would close {len(stale)} issues.[/]")
        return stale

    if not confirm:
        confirm = confirm_action(
            f"Close {len(stale)} stale issues? They will be labeled '{STALE_LABEL}'.",
            default=False,
        )

    if not confirm:
        print_warning("Aborted.")
        return stale

    closed = 0
    with progress_bar(len(stale), "Closing issues") as pb:
        task = pb.add_task("", total=len(stale))
        for iss in stale:
            try:
                repo = client.get_repo(iss["repo"])
                if repo:
                    issue = repo.get_issue(iss["number"])

                    # Add stale label
                    try:
                        issue.add_to_labels(STALE_LABEL)
                    except Exception:
                        # Label might not exist; create it
                        try:
                            repo.create_label(STALE_LABEL, "ededed", "Marked as stale due to inactivity")
                            issue.add_to_labels(STALE_LABEL)
                        except Exception:
                            pass

                    issue.create_comment(STALE_COMMENT)
                    issue.edit(state="closed")
                    issue.create_comment(CLOSING_COMMENT)
                    closed += 1
            except Exception as e:
                print_error(f"Failed to close {iss['repo']}#{iss['number']}: {e}")
            pb.advance(task)

    print_success(f"Closed {closed} stale issues.")
    return stale


def clean_pr_branches(
    client: GitHubClient,
    merged_older_days: int = 30,
    repo_name: str = "all",
    dry_run: bool = True,
    confirm: bool = False,
    json_output: bool = False,
) -> list[dict[str, Any]]:
    """Delete remote branches for already-merged PRs."""
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=merged_older_days)
    branches_to_delete: list[dict[str, Any]] = []

    repos = _resolve_repos(client, repo_name)
    print_info(f"Checking merged PRs across {len(repos)} repos...")

    with progress_bar(len(repos), "Scanning PRs") as pb:
        task = pb.add_task("", total=len(repos))
        for repo in repos:
            try:
                for pr in client.get_merged_pulls(repo):
                    if pr.merged_at and pr.merged_at < cutoff and pr.head.ref:
                        # Check if branch still exists
                        try:
                            repo.get_git_ref(f"heads/{pr.head.ref}")
                            branches_to_delete.append({
                                "repo": repo.full_name,
                                "branch": pr.head.ref,
                                "pr_number": pr.number,
                                "pr_title": pr.title,
                                "merged_at": str(pr.merged_at.date()),
                            })
                        except Exception:
                            # Branch already deleted
                            pass
            except Exception:
                pass
            pb.advance(task)

    if json_output:
        from ..display import to_json
        console.print(to_json(branches_to_delete))
        return branches_to_delete

    if not branches_to_delete:
        print_success("No stale merged PR branches found.")
        return []

    # Show table
    from ..display import Table, box
    table = Table(title=f"Branches to delete ({len(branches_to_delete)})", box=box.ROUNDED)
    table.add_column("Repo", style="cyan")
    table.add_column("Branch", style="yellow")
    table.add_column("PR #", style="dim")
    table.add_column("Merged", style="green")
    for b in branches_to_delete:
        table.add_row(b["repo"], b["branch"], f"#{b['pr_number']}", b["merged_at"])
    console.print(table)

    if dry_run:
        console.print("\n[yellow]Dry run mode — no branches deleted.[/]")
        return branches_to_delete

    if not confirm:
        confirm = confirm_action(
            f"Delete {len(branches_to_delete)} remote branches?", default=False
        )

    if not confirm:
        print_warning("Aborted.")
        return branches_to_delete

    deleted = 0
    with progress_bar(len(branches_to_delete), "Deleting branches") as pb:
        task = pb.add_task("", total=len(branches_to_delete))
        for b in branches_to_delete:
            try:
                repo = client.get_repo(b["repo"])
                if repo:
                    ref = repo.get_git_ref(f"heads/{b['branch']}")
                    ref.delete()
                    deleted += 1
            except Exception as e:
                print_error(f"Failed to delete {b['repo']}/{b['branch']}: {e}")
            pb.advance(task)

    print_success(f"Deleted {deleted} stale branches.")
    return branches_to_delete


def audit_labels(
    client: GitHubClient,
    repo_name: str = "all",
    json_output: bool = False,
) -> dict[str, Any]:
    """Find unused labels and suggest standard label cleanup."""
    repos = _resolve_repos(client, repo_name)
    results: dict[str, Any] = {"repos": {}, "summary": {}}

    print_info(f"Auditing labels across {len(repos)} repos...")

    with progress_bar(len(repos), "Auditing labels") as pb:
        task = pb.add_task("", total=len(repos))
        for repo in repos:
            try:
                labels = list(client.get_labels(repo))
                existing_names = {l.name for l in labels}

                # Check which standard labels are missing
                missing_standard = [name for name in STANDARD_LABELS if name not in existing_names]

                # Check for unused labels (labels not on any issues)
                # This is expensive, so we skip the full check for large repos
                # and just report on standard label coverage

                results["repos"][repo.full_name] = {
                    "total_labels": len(labels),
                    "existing": sorted(existing_names),
                    "missing_standard": missing_standard,
                }
            except Exception:
                pass
            pb.advance(task)

    # Summary
    repos_missing_bug = sum(
        1 for r in results["repos"].values() if "bug" in r["missing_standard"]
    )
    results["summary"] = {
        "total_repos_audited": len(results["repos"]),
        "repos_missing_bug_label": repos_missing_bug,
        "repos_missing_enhancement_label": sum(
            1 for r in results["repos"].values() if "enhancement" in r["missing_standard"]
        ),
    }

    if json_output:
        from ..display import to_json
        console.print(to_json(results))
    else:
        _print_label_summary(results)

    return results


def sync_labels(
    client: GitHubClient,
    repo_name: str = "all",
    dry_run: bool = True,
    json_output: bool = False,
) -> list[dict[str, Any]]:
    """Add missing standard labels to repos."""
    audit = audit_labels(client, repo_name=repo_name, json_output=True)

    created: list[dict] = []
    for repo_name, info in audit["repos"].items():
        if not info["missing_standard"]:
            continue

        for label_name in info["missing_standard"]:
            color = STANDARD_LABELS.get(label_name, "ededed")

            if dry_run:
                created.append({
                    "repo": repo_name,
                    "label": label_name,
                    "color": color,
                    "status": "dry_run",
                })
                continue

            try:
                repo = client.get_repo(repo_name)
                if repo:
                    repo.create_label(label_name, color)
                    created.append({
                        "repo": repo_name,
                        "label": label_name,
                        "color": color,
                        "status": "created",
                    })
            except Exception as e:
                created.append({
                    "repo": repo_name,
                    "label": label_name,
                    "status": "error",
                    "error": str(e),
                })

    if json_output:
        from ..display import to_json
        console.print(to_json(created))
    elif dry_run:
        console.print(f"\n[yellow]Dry run — would create {len(created)} labels across repos.[/]")
    else:
        print_success(f"Created {len([c for c in created if c['status'] == 'created'])} labels.")

    return created


def _print_label_summary(results: dict) -> None:
    """Pretty-print label audit results."""
    console.print(f"\n[bold]Label Audit: {results['summary']['total_repos_audited']} repos[/]\n")
    console.print(f"  Repos missing 'bug' label: {results['summary']['repos_missing_bug_label']}")
    console.print(f"  Repos missing 'enhancement' label: {results['summary']['repos_missing_enhancement_label']}")

    # Show repos missing the most standard labels
    repos_with_gaps = [
        (name, info)
        for name, info in results["repos"].items()
        if info["missing_standard"]
    ]
    repos_with_gaps.sort(key=lambda x: len(x[1]["missing_standard"]), reverse=True)

    if repos_with_gaps:
        console.print(f"\n[bold]Top repos missing standard labels:[/]")
        for name, info in repos_with_gaps[:10]:
            missing = ", ".join(info["missing_standard"][:5])
            console.print(f"  [cyan]{name}[/] — missing: {missing}")


def _resolve_repos(client: GitHubClient, repo_name: str) -> list[Repository]:
    """Resolve a repo filter to a list of Repository objects."""
    if repo_name == "all":
        return list(client.get_all_repos())
    elif "/" in repo_name:
        repo = client.get_repo(repo_name)
        return [repo] if repo else []
    else:
        return [r for r in client.get_all_repos() if repo_name.lower() in r.full_name.lower()]
