"""Display utilities using Rich for tables, progress bars, and formatting."""

from __future__ import annotations

import json
from typing import Any, Optional

from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.panel import Panel
from rich.markdown import Markdown
from rich.prompt import Confirm
from rich import box

console = Console()


def print_markdown(text: str) -> None:
    """Render markdown in the terminal."""
    console.print(Markdown(text))


def print_panel(text: str, title: str = "", style: str = "") -> None:
    """Print text inside a bordered panel."""
    console.print(Panel(text, title=title, style=style))


def print_error(text: str) -> None:
    """Print an error message."""
    console.print(f"[bold red]✗[/] {text}")


def print_success(text: str) -> None:
    """Print a success message."""
    console.print(f"[bold green]✓[/] {text}")


def print_warning(text: str) -> None:
    """Print a warning message."""
    console.print(f"[bold yellow]⚠[/] {text}")


def print_info(text: str) -> None:
    """Print an informational message."""
    console.print(f"[bold blue]ℹ[/] {text}")


def confirm_action(prompt: str, default: bool = False) -> bool:
    """Ask the user for confirmation."""
    return Confirm.ask(f"[bold yellow]{prompt}[/]", default=default)


def repo_table(repos: list[dict[str, Any]], title: str = "Repositories") -> Table:
    """Create a Rich table of repo summaries."""
    table = Table(title=title, box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Visibility", style="dim")
    table.add_column("Default Branch")
    table.add_column("Last Push", style="green")
    table.add_column("Archived")
    table.add_column("Language")

    for r in repos:
        archived = "[red]yes[/]" if r.get("archived") else "[dim]no[/]"
        table.add_row(
            r.get("name", ""),
            r.get("visibility", "unknown"),
            r.get("default_branch", ""),
            r.get("last_push", ""),
            archived,
            r.get("language", "") or "",
        )
    return table


def issue_table(issues: list[dict[str, Any]], title: str = "Issues") -> Table:
    """Create a Rich table of issue summaries."""
    table = Table(title=title, box=box.ROUNDED, header_style="bold cyan")
    table.add_column("#", style="dim")
    table.add_column("Title")
    table.add_column("Repo", style="cyan")
    table.add_column("Created")
    table.add_column("Updated")
    table.add_column("Labels")

    for iss in issues:
        table.add_row(
            str(iss.get("number", "")),
            iss.get("title", ""),
            iss.get("repo", ""),
            iss.get("created_at", ""),
            iss.get("updated_at", ""),
            ", ".join(iss.get("labels", [])) or "-",
        )
    return table


def file_audit_table(files: list[dict[str, Any]], title: str = "File Audit") -> Table:
    """Create a Rich table of file issues."""
    table = Table(title=title, box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Repo", style="cyan")
    table.add_column("Path")
    table.add_column("Issue", style="yellow")
    table.add_column("Detail")

    for f in files:
        table.add_row(
            f.get("repo", ""),
            f.get("path", ""),
            f.get("issue", ""),
            f.get("detail", ""),
        )
    return table


def progress_bar(total: int, description: str = "Processing") -> Progress:
    """Create a Rich progress bar."""
    return Progress(
        SpinnerColumn(),
        TextColumn(f"[bold blue]{description}[/]"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    )


def to_json(data: Any) -> str:
    """Format data as indented JSON."""
    return json.dumps(data, indent=2, default=str)


def output(data: Any, use_json: bool = False) -> None:
    """Unified output: JSON if requested, otherwise use Rich."""
    if use_json:
        console.print(to_json(data))
    elif isinstance(data, Table):
        console.print(data)
    elif isinstance(data, str):
        console.print(data)
    else:
        console.print(data)
