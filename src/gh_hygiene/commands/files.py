"""File commands: audit, clean, reorganize."""

from __future__ import annotations

import base64
import os
from typing import Any, Optional

import yaml
from github.Repository import Repository

from ..client import GitHubClient
from ..display import (
    console,
    file_audit_table,
    print_success,
    print_warning,
    print_error,
    print_info,
    confirm_action,
    progress_bar,
)

CLUTTER_PATTERNS = [
    ".DS_Store",
    "Thumbs.db",
    "*.pyc",
    "__pycache__/*",
    "*.pyo",
    ".env.local",
    ".env.*.local",
    "npm-debug.log*",
    "yarn-debug.log*",
    "yarn-error.log*",
    "*.orig",
    "*.bak",
    "*.swp",
    "*~",
]

LARGE_FILE_THRESHOLD = 1_000_000  # 1MB


def audit_files(
    client: GitHubClient,
    repo_name: str = "all",
    json_output: bool = False,
) -> list[dict[str, Any]]:
    """Scan repos for file clutter: large files, temp files, .DS_Store, inconsistent naming."""
    issues = []

    repos = _resolve_repos(client, repo_name)
    if not repos:
        print_warning(f"No repos found matching '{repo_name}'")
        return []

    print_info(f"Auditing files across {len(repos)} repos...")

    with progress_bar(len(repos), "Scanning files") as pb:
        task = pb.add_task("", total=len(repos))
        for repo in repos:
            try:
                contents = repo.get_contents("")
                _scan_directory(repo, contents, "", issues)
            except Exception as e:
                # Skip empty repos or access errors
                pass
            pb.advance(task)

    if json_output:
        from ..display import to_json
        console.print(to_json(issues))
    else:
        if issues:
            table = file_audit_table(issues, title=f"File Issues ({len(issues)})")
            console.print(table)
        else:
            print_success("No file issues found!")

    return issues


def _scan_directory(
    repo: Repository,
    contents: list,
    prefix: str,
    issues: list,
    depth: int = 0,
):
    """Recursively scan a directory for issues."""
    if depth > 5:  # Safety limit
        return

    for item in contents:
        path = f"{prefix}/{item.name}" if prefix else item.name

        try:
            if item.type == "dir":
                # Check for empty dirs
                try:
                    sub_contents = repo.get_contents(item.path)
                    if not sub_contents:
                        issues.append({
                            "repo": repo.full_name,
                            "path": path,
                            "issue": "Empty directory",
                            "detail": "Directory contains no files",
                        })
                    else:
                        _scan_directory(repo, sub_contents, path, issues, depth + 1)
                except Exception:
                    pass

            elif item.type == "file":
                # Check for clutter files
                name = item.name
                for pattern in CLUTTER_PATTERNS:
                    if _matches_pattern(name, pattern):
                        issues.append({
                            "repo": repo.full_name,
                            "path": path,
                            "issue": "Clutter file",
                            "detail": f"Matches pattern '{pattern}'",
                        })
                        break

                # Check for large files
                if item.size > LARGE_FILE_THRESHOLD:
                    size_mb = item.size / 1_000_000
                    issues.append({
                        "repo": repo.full_name,
                        "path": path,
                        "issue": "Large file",
                        "detail": f"{size_mb:.1f} MB",
                    })

                # Check for merge conflict markers in text files
                if item.size < 500_000 and item.name.endswith((".py", ".js", ".ts", ".jsx", ".tsx", ".md", ".txt", ".yml", ".yaml", ".json", ".html", ".css", ".rb", ".go", ".rs", ".java", ".c", ".cpp", ".h")):
                    try:
                        content = base64.b64decode(item.content).decode("utf-8", errors="ignore")
                        if "<<<<<<<" in content or ">>>>>>>" in content:
                            issues.append({
                                "repo": repo.full_name,
                                "path": path,
                                "issue": "Merge conflict markers",
                                "detail": "Contains unresolved merge conflicts",
                            })
                    except Exception:
                        pass

        except Exception:
            pass


def _matches_pattern(filename: str, pattern: str) -> bool:
    """Simple glob-style pattern matching."""
    if pattern == filename:
        return True
    if pattern.startswith("*."):
        return filename.endswith(pattern[1:])
    if pattern.startswith("*") and pattern.endswith("*"):
        return pattern[1:-1] in filename
    if pattern.endswith("*"):
        return filename.startswith(pattern[:-1])
    return False


def clean_files(
    client: GitHubClient,
    repo_name: str = "all",
    dry_run: bool = True,
    confirm: bool = False,
    json_output: bool = False,
) -> list[dict[str, Any]]:
    """Remove common clutter files across repos."""
    repos = _resolve_repos(client, repo_name)
    if not repos:
        print_warning(f"No repos found matching '{repo_name}'")
        return []

    # First, find all clutter files
    all_deletions = []

    print_info(f"Scanning {len(repos)} repos for clutter...")
    with progress_bar(len(repos), "Scanning") as pb:
        task = pb.add_task("", total=len(repos))
        for repo in repos:
            try:
                contents = repo.get_contents("")
                _find_clutter(repo, contents, "", all_deletions)
            except Exception:
                pass
            pb.advance(task)

    if not all_deletions:
        print_success("No clutter files found!")
        return []

    if json_output:
        from ..display import to_json
        console.print(to_json(all_deletions))
        return all_deletions

    # Show what would be deleted
    table = file_audit_table(all_deletions, title=f"Files to clean ({len(all_deletions)})")
    console.print(table)

    if dry_run:
        console.print("\n[yellow]Dry run mode — no changes made.[/]")
        return all_deletions

    if not confirm:
        confirm = confirm_action(f"Delete {len(all_deletions)} clutter files?", default=False)

    if not confirm:
        print_warning("Aborted.")
        return all_deletions

    # Execute deletions
    deleted = 0
    with progress_bar(len(all_deletions), "Cleaning") as pb:
        task = pb.add_task("", total=len(all_deletions))
        for item in all_deletions:
            try:
                repo = client.get_repo(item["repo"])
                if repo:
                    contents = repo.get_contents(item["path"])
                    repo.delete_file(
                        item["path"],
                        f"chore: remove {os.path.basename(item['path'])}",
                        contents.sha,
                    )
                    deleted += 1
            except Exception as e:
                print_error(f"Failed to delete {item['repo']}/{item['path']}: {e}")
            pb.advance(task)

    print_success(f"Deleted {deleted} clutter files.")
    return all_deletions


def _find_clutter(repo: Repository, contents: list, prefix: str, deletions: list, depth: int = 0):
    """Recursively find clutter files."""
    if depth > 5:
        return
    for item in contents:
        path = f"{prefix}/{item.name}" if prefix else item.name
        try:
            if item.type == "dir":
                sub = repo.get_contents(item.path)
                _find_clutter(repo, sub, path, deletions, depth + 1)
            elif item.type == "file":
                for pattern in [".DS_Store", "Thumbs.db"]:
                    if _matches_pattern(item.name, pattern):
                        deletions.append({
                            "repo": repo.full_name,
                            "path": path,
                            "issue": "Clutter file",
                            "detail": item.name,
                        })
        except Exception:
            pass


def reorganize_files(
    client: GitHubClient,
    repo_name: str,
    operations: list[dict[str, str]],
    dry_run: bool = True,
    confirm: bool = False,
    json_output: bool = False,
) -> list[dict[str, Any]]:
    """Apply move/rename/delete operations according to rules."""
    repo = client.get_repo(repo_name)
    if not repo:
        print_error(f"Repo '{repo_name}' not found.")
        return []

    results = []

    print_info(f"Reorganizing {repo_name}: {len(operations)} operations")

    for op in operations:
        action = op.get("action", "")
        from_path = op.get("from_path", op.get("from", ""))
        to_path = op.get("to_path", op.get("to", ""))
        message = op.get("commit_message", f"chore: {action} {from_path}")

        if json_output:
            results.append({"action": action, "from": from_path, "to": to_path, "message": message})
            continue

        if dry_run:
            console.print(f"  [dim]Would {action}: {from_path} -> {to_path}[/]")
            results.append({"action": action, "from": from_path, "to": to_path, "status": "dry_run"})
            continue

        try:
            if action == "move" or action == "rename":
                contents = repo.get_contents(from_path)
                if to_path:
                    repo.create_file(
                        to_path,
                        message,
                        base64.b64decode(contents.content),
                    )
                repo.delete_file(from_path, message, contents.sha)
                print_success(f"{action}d: {from_path} -> {to_path}")
                results.append({"action": action, "from": from_path, "to": to_path, "status": "done"})

            elif action == "delete":
                contents = repo.get_contents(from_path)
                repo.delete_file(from_path, message, contents.sha)
                print_success(f"Deleted: {from_path}")
                results.append({"action": action, "from": from_path, "status": "done"})

        except Exception as e:
            print_error(f"Failed {action} {from_path}: {e}")
            results.append({"action": action, "from": from_path, "status": "error", "error": str(e)})

    if json_output:
        from ..display import to_json
        console.print(to_json(results))

    return results


def load_reorg_rules(rules_path: str) -> list[dict[str, Any]]:
    """Load reorganization rules from a YAML file."""
    with open(rules_path) as f:
        rules = yaml.safe_load(f)
    return rules.get("operations", [])


def apply_reorg_rules(
    client: GitHubClient,
    rules_path: str,
    repo_filter: str = "all",
    dry_run: bool = True,
    json_output: bool = False,
) -> list[dict[str, Any]]:
    """Load rules from YAML and apply them across repos."""
    rules = load_reorg_rules(rules_path)
    if not rules:
        print_warning("No operations found in rules file.")
        return []

    print_info(f"Loaded {len(rules)} operations from {rules_path}")

    # Group rules by repo
    by_repo: dict[str, list] = {}
    wildcard_ops = []
    for rule in rules:
        target = rule.get("repo", "all")
        if target == "*" or target == "all":
            wildcard_ops.append(rule)
        else:
            by_repo.setdefault(target, []).append(rule)

    # Expand wildcard ops
    repos = _resolve_repos(client, repo_filter)
    for repo in repos:
        if repo.full_name not in by_repo:
            by_repo[repo.full_name] = []
        by_repo[repo.full_name].extend(wildcard_ops)

    all_results = []
    for repo_name, ops in by_repo.items():
        if not ops:
            continue
        results = reorganize_files(client, repo_name, ops, dry_run=dry_run, json_output=json_output)
        all_results.extend(results)

    return all_results


def _resolve_repos(client: GitHubClient, repo_name: str) -> list[Repository]:
    """Resolve a repo filter to a list of Repository objects."""
    if repo_name == "all":
        return list(client.get_all_repos())
    elif "/" in repo_name:
        repo = client.get_repo(repo_name)
        return [repo] if repo else []
    else:
        # Partial name match
        return [r for r in client.get_all_repos() if repo_name.lower() in r.full_name.lower()]
