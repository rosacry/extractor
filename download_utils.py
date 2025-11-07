from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping, Sequence

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table

console = Console()

PROGRESS_COLUMNS = (
    SpinnerColumn(style="cyan"),
    TextColumn("{task.description}", style="cyan"),
    BarColumn(bar_width=None, complete_style="bright_magenta", finished_style="bright_magenta"),
    DownloadColumn(),
    TransferSpeedColumn(),
    TimeRemainingColumn(),
    TimeElapsedColumn(),
)


def ensure_directory(path: Path) -> None:
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)


def gather_existing_files(directories: Sequence[Path]) -> set[str]:
    names: set[str] = set()
    for directory in directories:
        if not directory:
            continue
        expanded = directory.expanduser()
        if expanded.exists() and expanded.is_dir():
            for entry in expanded.iterdir():
                if entry.is_file():
                    names.add(entry.name.lower())
    return names


def format_bytes(num_bytes: int) -> str:
    if num_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    size = float(num_bytes)
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024
        idx += 1
    return f"{size:.2f} {units[idx]}"


def render_overview(
    source_name: str,
    *,
    total_links: int,
    existing_count: int,
    missing_count: int,
    target_dir: Path,
) -> None:
    lines = [
        f"[bold cyan]{source_name}[/] catalogue check",
        f"[bold]{total_links}[/] archive(s) discovered on the site.",
        f"[bold green]{existing_count}[/] already stored in [italic]{target_dir}[/] or sibling folders.",
        f"[bold magenta]{missing_count}[/] left to download.",
    ]
    console.print(
        Panel.fit(
            "\n".join(lines),
            title=f"{source_name} Harvest",
            border_style="cyan",
        )
    )


def render_remaining_counter(count: int) -> None:
    message = "All caught up!" if count == 0 else f"[bold magenta]{count}[/] archive(s) still pending."
    console.print(
        Panel.fit(
            message,
            title="Remaining Downloads",
            border_style="magenta" if count else "green",
        )
    )


def build_table(rows: Iterable[tuple[str, str]], title: str) -> Table:
    table = Table(title=title, show_lines=False)
    table.add_column("File", style="bold yellow", overflow="fold")
    table.add_column("Source", style="magenta", overflow="fold")
    for filename, url in rows:
        table.add_row(filename, url)
    return table


def build_summary_table(rows: Iterable[tuple[str, str, str]]) -> Table:
    table = Table(title="Download Summary", show_lines=False)
    table.add_column("File", style="bold", overflow="fold")
    table.add_column("Status", style="bold")
    table.add_column("Details", overflow="fold")
    for filename, status, detail in rows:
        table.add_row(filename, status, detail)
    return table


def download_with_progress(
    session,
    url: str,
    destination: Path,
    *,
    overwrite: bool,
    progress: Progress | None,
    headers: Mapping[str, str] | None,
) -> tuple[bool, int]:
    if destination.exists() and not overwrite:
        return False, 0

    request_headers = dict(headers) if headers else {}

    with session.get(url, headers=request_headers, stream=True, timeout=60) as response:
        response.raise_for_status()
        header_length = response.headers.get("Content-Length")
        try:
            total_bytes = int(header_length) if header_length else None
        except ValueError:
            total_bytes = None

        task_id = None
        if progress is not None:
            task_id = progress.add_task(
                f"[cyan]{destination.name}", total=total_bytes if total_bytes else None
            )

        bytes_written = 0
        with destination.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                handle.write(chunk)
                bytes_written += len(chunk)
                if task_id is not None:
                    progress.update(task_id, advance=len(chunk))

        if task_id is not None and total_bytes:
            progress.update(task_id, completed=total_bytes)

    if task_id is not None and progress is not None:
        progress.remove_task(task_id)

    return True, bytes_written
