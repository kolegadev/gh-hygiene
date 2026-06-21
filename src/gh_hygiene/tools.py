"""Tool definitions for DeepSeek function calling.

Each tool is defined as an OpenAI-compatible function schema and mapped
to a Python callable that implements the actual logic.

Tools are categorized:
  - READ_ONLY: Can execute without user confirmation
  - DESTRUCTIVE: Must be called with dry_run=True first, then confirmed
"""

from __future__ import annotations

from typing import Any, Callable, Optional

DESTRUCTIVE_TOOLS = {
    "archive_repos",
    "close_stale_issues",
    "clean_pr_branches",
    "clean_files",
    "reorganize_files",
    "change_visibility",
    "run_shell_command",
}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    # ---- Repos ----
    {
        "type": "function",
        "function": {
            "name": "list_repos",
            "description": "List all GitHub repos with key metadata (name, visibility, last push, language, stars, etc.)",
            "parameters": {
                "type": "object",
                "properties": {
                    "sort_by": {
                        "type": "string",
                        "enum": ["name", "pushed", "created", "stars"],
                        "description": "Sort order for the repo list",
                    },
                    "filter_visibility": {
                        "type": "string",
                        "enum": ["all", "public", "private"],
                        "description": "Filter by visibility",
                    },
                    "filter_archived": {
                        "type": "boolean",
                        "description": "Filter by archived status (omit for all)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "audit_repos",
            "description": "Audit repos for hygiene issues: no description, no README, wrong default branch, inactivity > 1 year, missing license, no topics. Returns categorized results (critical, warnings, ok).",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_filter": {
                        "type": "string",
                        "description": "Repo name pattern or 'all'",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "archive_repos",
            "description": "Archive repos with no activity older than a threshold. DESTRUCTIVE — always call with dry_run=True first to preview, then ask the user for confirmation before calling with dry_run=False.",
            "parameters": {
                "type": "object",
                "properties": {
                    "older_than_days": {
                        "type": "integer",
                        "description": "Days since last push to consider a repo stale (e.g. 365 for 1 year)",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true, only preview what would be archived without making changes",
                    },
                },
                "required": ["older_than_days", "dry_run"],
            },
        },
    },
    # ---- Files ----
    {
        "type": "function",
        "function": {
            "name": "audit_files",
            "description": "Scan repos for file clutter: large files (>1MB), temp files (.DS_Store, Thumbs.db), merge conflict markers, empty directories. Returns a list of issues found.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repo name (e.g. 'owner/repo'), partial name match, or 'all'",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clean_files",
            "description": "Remove common clutter files (.DS_Store, Thumbs.db) across repos. DESTRUCTIVE — always call with dry_run=True first to preview, then confirm with user before dry_run=False.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repo name or 'all'",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true, only preview what would be deleted",
                    },
                },
                "required": ["repo", "dry_run"],
            },
        },
    },
    # ---- Hygiene ----
    {
        "type": "function",
        "function": {
            "name": "list_stale_issues",
            "description": "Find open issues with no activity for N days across repos.",
            "parameters": {
                "type": "object",
                "properties": {
                    "stale_days": {
                        "type": "integer",
                        "description": "Days of inactivity to consider stale (default 90)",
                    },
                    "repo": {
                        "type": "string",
                        "description": "Repo name or 'all'",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_stale_issues",
            "description": "Add 'stale' label, comment, and close issues inactive for N days. DESTRUCTIVE — always call with dry_run=True first to preview, then confirm with user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "stale_days": {
                        "type": "integer",
                        "description": "Days of inactivity (default 90)",
                    },
                    "repo": {
                        "type": "string",
                        "description": "Repo name or 'all'",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true, only preview what would be closed",
                    },
                },
                "required": ["stale_days", "repo", "dry_run"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clean_pr_branches",
            "description": "Delete remote branches for already-merged PRs older than N days. DESTRUCTIVE — always call with dry_run=True first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "merged_older_days": {
                        "type": "integer",
                        "description": "Days since merge to consider stale (default 30)",
                    },
                    "repo": {
                        "type": "string",
                        "description": "Repo name or 'all'",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true, only preview what would be deleted",
                    },
                },
                "required": ["merged_older_days", "repo", "dry_run"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "audit_labels",
            "description": "Audit labels across repos — find which standard labels (bug, enhancement, documentation, etc.) are missing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repo name or 'all'",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_rate_limit",
            "description": "Check current GitHub API rate limit status (remaining requests, reset time).",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "change_visibility",
            "description": "Change repo visibility between public and private. Falls back to gh CLI if PyGithub fails. DESTRUCTIVE — always call with dry_run=True first to preview, then ask user for confirmation before dry_run=False.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repos": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of repo full names (e.g. 'owner/repo') to change visibility for",
                    },
                    "visibility": {
                        "type": "string",
                        "enum": ["private", "public"],
                        "description": "Target visibility",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true, only preview what would be changed without making changes",
                    },
                },
                "required": ["repos", "visibility", "dry_run"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell_command",
            "description": "Run a shell command on the user's machine as a fallback when no dedicated tool exists. Use this for operations not covered by other tools (e.g. gh CLI commands, git operations). Returns stdout and stderr. DESTRUCTIVE — always call with dry_run=True first to preview the command, then ask for confirmation before dry_run=False.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to run",
                    },
                    "description": {
                        "type": "string",
                        "description": "Human-readable description of what the command does",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true, only show the command without executing it",
                    },
                },
                "required": ["command", "description", "dry_run"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# run_shell_command — CLI fallback tool
# ---------------------------------------------------------------------------

def run_shell_command(command: str, description: str = "", dry_run: bool = True, **kwargs) -> dict:
    """Run a shell command as a fallback for operations not covered by other tools.

    In dry-run mode, just returns the command without executing.
    When confirmed, runs the command and returns stdout/stderr.
    """
    result = {
        "command": command,
        "description": description,
    }

    if dry_run:
        result["status"] = "dry_run"
        result["message"] = f"Would run: {command}"
        return result

    import subprocess

    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        result["status"] = "success" if proc.returncode == 0 else "error"
        result["returncode"] = proc.returncode
        result["stdout"] = proc.stdout[:5000]  # Truncate for LLM context
        result["stderr"] = proc.stderr[:2000]
        if proc.returncode != 0:
            result["message"] = f"Command failed with exit code {proc.returncode}"
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        result["message"] = "Command timed out after 120s"
    except Exception as e:
        result["status"] = "error"
        result["message"] = str(e)

    return result


# ---------------------------------------------------------------------------
# Late binding function registry — populated at runtime by the CLI
# ---------------------------------------------------------------------------

_function_registry: dict[str, Callable] = {}


def register_tool(name: str, fn: Callable) -> None:
    """Register a function implementation for a tool name."""
    _function_registry[name] = fn


def get_tool_function(name: str) -> Optional[Callable]:
    """Get the registered function for a tool name."""
    return _function_registry.get(name)


def is_destructive(tool_name: str) -> bool:
    """Check if a tool is destructive (requires confirmation)."""
    return tool_name in DESTRUCTIVE_TOOLS


def is_read_only(tool_name: str) -> bool:
    """Check if a tool is read-only (safe to run without confirmation)."""
    return tool_name not in DESTRUCTIVE_TOOLS
