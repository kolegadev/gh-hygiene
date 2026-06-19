"""Main CLI entry point for gh-hygiene.

Provides four modes:
  gh-hygiene chat       — Interactive LLM-powered chat (terminal)
  gh-hygiene serve      — Start the web UI on localhost:8080
  gh-hygiene run "..."  — One-shot natural language instruction
  gh-hygiene <command>  — Direct access to underlying commands
"""

from __future__ import annotations

import sys
from typing import Optional

import typer
from rich.prompt import Confirm

from .auth import (
    get_github_token,
    get_deepseek_api_key,
    get_token_source,
    get_deepseek_source,
    store_github_token,
    store_deepseek_key,
    clear_stored_tokens,
)
from .client import GitHubClient
from .commands.repos import list_repos, audit_repos, archive_repos
from .commands.files import audit_files, clean_files, reorganize_files, apply_reorg_rules
from .commands.hygiene import (
    list_stale_issues,
    close_stale_issues,
    clean_pr_branches,
    audit_labels,
    sync_labels,
)
from .chat import ChatSession, run_one_shot
from .display import (
    console,
    print_error,
    print_success,
    print_info,
    print_warning,
    print_markdown,
    print_panel,
    confirm_action,
)
from .tools import register_tool

app = typer.Typer(
    name="gh-hygiene",
    help="AI-powered GitHub repo hygiene manager",
    no_args_is_help=True,
)

# Subcommand groups
repos_app = typer.Typer(help="Repo management commands")
files_app = typer.Typer(help="File management commands")
hygiene_app = typer.Typer(help="Issue/PR hygiene commands")
auth_app = typer.Typer(help="Authentication management")

app.add_typer(repos_app, name="repos")
app.add_typer(files_app, name="files")
app.add_typer(hygiene_app, name="hygiene")
app.add_typer(auth_app, name="auth")

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _get_gh_client() -> Optional[GitHubClient]:
    """Get an authenticated GitHub client, or print error and exit."""
    token = get_github_token()
    if not token:
        print_error("No GitHub token found. Run 'gh-hygiene auth setup' first.")
        print_info("Token sources checked: gh CLI, macOS Keychain, $GITHUB_TOKEN, config file.")
        raise typer.Exit(1)
    return GitHubClient(token)


def _require_auth():
    """Ensure both GitHub and DeepSeek are configured."""
    gh = get_github_token()
    ds = get_deepseek_api_key()
    if not gh or not ds:
        print_error("Authentication not fully configured.")
        print_info("Run 'gh-hygiene auth setup' to configure GitHub and DeepSeek credentials.")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Register tool functions for LLM function calling
# ---------------------------------------------------------------------------


def _register_tools(client: GitHubClient):
    """Register all tool function implementations."""
    register_tool("list_repos", lambda **kw: list_repos(client, **kw))
    register_tool("audit_repos", lambda **kw: audit_repos(client, **kw))
    register_tool("archive_repos", lambda **kw: archive_repos(client, **kw))
    register_tool("audit_files", lambda **kw: audit_files(client, **kw))
    register_tool("clean_files", lambda **kw: clean_files(client, **kw))
    register_tool("reorganize_files", lambda **kw: reorganize_files(client, **kw))
    register_tool("list_stale_issues", lambda **kw: list_stale_issues(client, **kw))
    register_tool("close_stale_issues", lambda **kw: close_stale_issues(client, **kw))
    register_tool("clean_pr_branches", lambda **kw: clean_pr_branches(client, **kw))
    register_tool("audit_labels", lambda **kw: audit_labels(client, **kw))
    register_tool("get_rate_limit", lambda **kw: client.get_rate_limit())


# ---------------------------------------------------------------------------
# Chat mode
# ---------------------------------------------------------------------------


@app.command()
def chat():
    """Start an interactive chat session with the AI assistant.

    Chat naturally about your repos — the AI will figure out what to do.
    Type 'exit' or 'quit' to end the session.
    Type 'y' or 'yes' to confirm pending destructive actions.
    Type 'n' or 'no' to reject them.
    """
    _require_auth()

    gh_token = get_github_token()
    ds_key = get_deepseek_api_key()
    client = GitHubClient(gh_token)
    _register_tools(client)

    session = ChatSession(ds_key, client)

    print_panel(
        "[bold cyan]gh-hygiene chat mode[/]  \n"
        "[dim]AI: DeepSeek V4 Flash | GitHub: " + get_token_source() + "[/]\n\n"
        "Type [bold]'exit'[/] to quit, [bold]'help'[/] for ideas.\n"
        "Just tell me what you want to do with your repos!",
        title="🤖 Welcome",
    )

    while True:
        try:
            user_input = typer.prompt("\n[bold green]You[/]", prompt_suffix=" ")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye![/]")
            break

        user_input = user_input.strip()

        if user_input.lower() in ("exit", "quit", "q"):
            console.print("[dim]Goodbye![/]")
            break

        if user_input.lower() == "help":
            print_markdown("""
**Things you can ask me:**
- "Show me all my repos"
- "Which repos haven't been touched in over a year?"
- "Find stale issues across all my projects"
- "Clean up .DS_Store files from my repos"
- "Audit my repos and tell me what needs attention"
- "Archive any repo I haven't pushed to in 2 years"
- "Check which repos are missing README files"
- "What's my GitHub API rate limit status?"
""")
            continue

        # Handle confirmations/rejections
        if user_input.lower() in ("y", "yes"):
            response = session.confirm_pending()
            print_markdown(response)
            continue

        if user_input.lower() in ("n", "no"):
            response = session.reject_pending()
            print_markdown(response)
            continue

        # Normal message
        response = session.send_message(user_input)
        print_markdown(response)

        # Check if there are pending destructive actions to confirm
        if session._pending_destructive:
            tools = [info["tool_name"] for info in session._pending_destructive.values()]
            if confirm_action(f"Proceed with: {', '.join(tools)}?"):
                response = session.confirm_pending()
                print_markdown(response)
            else:
                response = session.reject_pending()
                print_markdown(response)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Host to bind to"),
    port: int = typer.Option(8080, help="Port to listen on"),
):
    """Start the web UI on localhost.

    Opens a browser-based chat interface at http://localhost:8080.
    Press Ctrl+C to stop the server.
    """
    _require_auth()

    # Lazy import to avoid circular issues
    from .server import start_server
    start_server(host=host, port=port)


@app.command()
def run(instruction: str = typer.Argument(..., help="Natural language instruction for the AI")):
    """Run a single natural language instruction (one-shot mode).

    Destructive operations are shown as dry-run previews only.
    Use 'chat' mode for interactive confirmation of destructive actions.

    Example:
        gh-hygiene run "find all repos without README files"
    """
    _require_auth()

    gh_token = get_github_token()
    ds_key = get_deepseek_api_key()
    client = GitHubClient(gh_token)
    _register_tools(client)

    print_info(f"Running: {instruction}")
    run_one_shot(ds_key, client, instruction)


# ---------------------------------------------------------------------------
# Auth commands
# ---------------------------------------------------------------------------


@auth_app.command(name="setup")
def auth_setup():
    """Interactive wizard to set up GitHub and DeepSeek credentials."""
    console.print("[bold]🔐 gh-hygiene Authentication Setup[/]\n")

    # GitHub PAT
    console.print("[bold]GitHub Personal Access Token[/]")
    console.print("[dim]Needs scopes: repo, delete_repo, admin:org (for org repos)[/]")
    console.print("[dim]Create one at: https://github.com/settings/tokens[/]\n")

    gh_token = typer.prompt("GitHub PAT", hide_input=True)

    if gh_token:
        store_github_token(gh_token)
        print_success("GitHub token stored in macOS Keychain.")

        # Verify
        try:
            gh = GitHubClient(gh_token)
            user = gh.user
            print_success(f"Verified! Authenticated as: [cyan]{user.login}[/]")
        except Exception as e:
            print_error(f"Token verification failed: {e}")
            print_warning("Token was stored but may not work. Check scopes and validity.")
    else:
        print_warning("Skipped GitHub token setup.")

    # DeepSeek API key
    console.print("\n[bold]DeepSeek API Key[/]")
    console.print("[dim]Get one at: https://platform.deepseek.com[/]\n")

    ds_key = typer.prompt("DeepSeek API Key", hide_input=True)

    if ds_key:
        store_deepseek_key(ds_key)
        print_success("DeepSeek API key stored in macOS Keychain.")
    else:
        print_warning("Skipped DeepSeek API key setup.")

    console.print("\n[bold green]✅ Setup complete![/]")
    console.print("Run [bold]gh-hygiene chat[/] to start managing your repos.")


@auth_app.command(name="status")
def auth_status():
    """Show authentication status."""
    gh = get_github_token()
    ds = get_deepseek_api_key()

    console.print("[bold]🔐 Authentication Status[/]\n")

    if gh:
        source = get_token_source()
        try:
            gh_client = GitHubClient(gh)
            user = gh_client.user
            rl = gh_client.get_rate_limit()
            print_success(f"GitHub: [cyan]{user.login}[/] (via {source})")
            print_info(f"  Rate limit: {rl['core_remaining']}/{rl['core_limit']} remaining")
            print_info(f"  Resets at: {rl['core_reset']}")
        except Exception as e:
            print_warning(f"GitHub: token found but verification failed ({e})")
    else:
        print_error("GitHub: not configured")

    if ds:
        source = get_deepseek_source()
        print_success(f"DeepSeek: configured (via {source})")
    else:
        print_error("DeepSeek: not configured")

    if gh and ds:
        console.print("\n[green]All systems go! Run 'gh-hygiene chat' to start.[/]")


@auth_app.command(name="clear")
def auth_clear():
    """Remove all stored credentials."""
    if confirm_action("Clear all stored GitHub and DeepSeek credentials?", default=False):
        clear_stored_tokens()
        print_success("All stored credentials cleared.")
    else:
        print_warning("Aborted.")


# ---------------------------------------------------------------------------
# Repo commands
# ---------------------------------------------------------------------------


@repos_app.command(name="list")
def repos_list(
    sort_by: str = typer.Option("name", help="Sort by: name, pushed, created, stars"),
    visibility: str = typer.Option("all", help="Filter: all, public, private"),
    archived: Optional[bool] = typer.Option(None, help="Filter by archived status"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """List all your GitHub repos."""
    client = _get_gh_client()
    list_repos(
        client,
        sort_by=sort_by,
        filter_visibility=visibility,
        filter_archived=archived,
        json_output=json_output,
    )


@repos_app.command(name="audit")
def repos_audit(
    repo_filter: str = typer.Option("all", help="Repo name pattern or 'all'"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Audit repos for hygiene issues."""
    client = _get_gh_client()
    audit_repos(client, repo_filter=repo_filter, json_output=json_output)


@repos_app.command(name="archive")
def repos_archive(
    older_than_days: int = typer.Option(365, help="Days since last push"),
    dry_run: bool = typer.Option(True, help="Preview only, don't make changes"),
    confirm: bool = typer.Option(False, "--confirm", help="Skip confirmation prompt"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Archive repos with no activity."""
    client = _get_gh_client()
    archive_repos(
        client,
        older_than_days=older_than_days,
        dry_run=dry_run,
        confirm=confirm,
        json_output=json_output,
    )


# ---------------------------------------------------------------------------
# File commands
# ---------------------------------------------------------------------------


@files_app.command(name="audit")
def files_audit(
    repo: str = typer.Option("all", help="Repo name or 'all'"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Scan repos for file clutter."""
    client = _get_gh_client()
    audit_files(client, repo_name=repo, json_output=json_output)


@files_app.command(name="clean")
def files_clean(
    repo: str = typer.Option("all", help="Repo name or 'all'"),
    dry_run: bool = typer.Option(True, help="Preview only"),
    confirm: bool = typer.Option(False, "--confirm", help="Skip confirmation"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Remove clutter files (.DS_Store, Thumbs.db)."""
    client = _get_gh_client()
    clean_files(client, repo_name=repo, dry_run=dry_run, confirm=confirm, json_output=json_output)


@files_app.command(name="reorg")
def files_reorg(
    repo: str = typer.Option("all", help="Repo name or 'all'"),
    rules: Optional[str] = typer.Option(None, help="Path to YAML rules file"),
    dry_run: bool = typer.Option(True, help="Preview only"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Apply file reorganization rules from a YAML file."""
    client = _get_gh_client()
    if rules:
        apply_reorg_rules(client, rules_path=rules, repo_filter=repo, dry_run=dry_run, json_output=json_output)
    else:
        print_warning("No rules file specified. Use --rules to provide a YAML file.")


# ---------------------------------------------------------------------------
# Hygiene commands
# ---------------------------------------------------------------------------


@hygiene_app.command(name="issues")
def hygiene_issues(
    stale_days: int = typer.Option(90, help="Days of inactivity to consider stale"),
    repo: str = typer.Option("all", help="Repo name or 'all'"),
    close: bool = typer.Option(False, "--close", help="Close stale issues"),
    dry_run: bool = typer.Option(True, help="Preview only"),
    confirm: bool = typer.Option(False, "--confirm", help="Skip confirmation"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """List or close stale issues."""
    client = _get_gh_client()
    if close:
        close_stale_issues(client, stale_days=stale_days, repo_name=repo, dry_run=dry_run, confirm=confirm, json_output=json_output)
    else:
        list_stale_issues(client, stale_days=stale_days, repo_name=repo, json_output=json_output)


@hygiene_app.command(name="prs")
def hygiene_prs(
    merged_older_days: int = typer.Option(30, help="Days since merge"),
    repo: str = typer.Option("all", help="Repo name or 'all'"),
    delete: bool = typer.Option(False, "--delete", help="Delete stale branches"),
    dry_run: bool = typer.Option(True, help="Preview only"),
    confirm: bool = typer.Option(False, "--confirm", help="Skip confirmation"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """List or clean merged PR branches."""
    client = _get_gh_client()
    clean_pr_branches(client, merged_older_days=merged_older_days, repo_name=repo, dry_run=dry_run, confirm=confirm, json_output=json_output)


@hygiene_app.command(name="labels")
def hygiene_labels(
    repo: str = typer.Option("all", help="Repo name or 'all'"),
    sync: bool = typer.Option(False, "--sync", help="Add missing standard labels"),
    dry_run: bool = typer.Option(True, help="Preview only"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Audit or sync labels."""
    client = _get_gh_client()
    if sync:
        sync_labels(client, repo_name=repo, dry_run=dry_run, json_output=json_output)
    else:
        audit_labels(client, repo_name=repo, json_output=json_output)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    app()


if __name__ == "__main__":
    main()
