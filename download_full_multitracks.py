#!/usr/bin/env python3
"""Download missing "Full Multitrack" archives from Cambridge MT.

The script scrapes the Cambridge MT multitrack landing page for links whose
anchor text contains "Full Multitrack", compares the linked archive names with
files already present in the target directory (including sibling folders), and
downloads only the missing ones. The terminal output is styled with Rich to make
progress feedback more pleasant.

Requirements:
    pip install -r requirements.txt

Example usage:
    python download_full_multitracks.py --target-dir data/cambridge

If Cloudflare or another protection blocks the initial page fetch, pass the
"--cookie" argument (or point "--cookie-file" at a text file) with the value of
the Cookie header copied from an authenticated browser session (after
completing the human verification step).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Mapping
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from rich.panel import Panel
from rich.progress import Progress

from download_utils import (
    PROGRESS_COLUMNS,
    build_summary_table,
    build_table,
    console,
    download_with_progress,
    ensure_directory,
    format_bytes,
    gather_existing_files,
    render_overview,
    render_remaining_counter,
)

DEFAULT_INDEX_URL = "https://cambridge-mt.com/ms3/mtk/"
DEFAULT_DELAY = 1.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def build_base_headers(user_agent: str, referer: str) -> dict[str, str]:
    """Return a browser-like header set tied to the supplied user agent."""

    # Keep the profile minimal and internally consistent to avoid Cloudflare's
    # heuristic mismatches. Client Hint headers are omitted because they must
    # mirror the browser that produced the cookie.
    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": referer,
        "Upgrade-Insecure-Requests": "1",
    }


def prompt_for_value(message: str) -> str:
    if not sys.stdin.isatty():
        return ""
    try:
        return input(message).strip()
    except EOFError:
        return ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--index-url",
        default=DEFAULT_INDEX_URL,
        help="Landing page that lists the multitrack downloads",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=Path("data/cambridge"),
        help="Directory where downloads are stored (default: data/cambridge)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help="Seconds to sleep between downloads (default: %.1f)" % DEFAULT_DELAY,
    )
    parser.add_argument(
        "--cookie",
        help="Optional cookie header value if the site requires prior verification",
    )
    parser.add_argument(
        "--cookie-file",
        type=Path,
        help="Path to a file containing the Cookie header value",
    )
    parser.add_argument(
        "--user-agent",
        help="Override the default User-Agent string with one copied from your browser",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List actions without downloading files",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-download files even if they already exist",
    )
    parser.add_argument(
        "--dump-index",
        type=Path,
        help="Write the fetched index HTML to this path for troubleshooting",
    )
    return parser.parse_args()


def load_index(
    session: requests.Session,
    url: str,
    cookie: str | None,
    base_headers: Mapping[str, str],
) -> str:
    headers = dict(base_headers)
    headers["Referer"] = url
    if cookie:
        headers["Cookie"] = cookie
    response = session.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    text = response.text
    if "Verifying you are human" in text:
        raise RuntimeError(
            "Site returned a human-verification page. Open the index URL in "
            "a browser, complete the verification, copy the Cookie header, "
            "and pass it via --cookie."
        )
    return text


def iterate_full_multitrack_links(html: str, base_url: str) -> Mapping[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    links: dict[str, str] = {}

    # Modern Cambridge MT markup stores the label "Full Multitrack" in a
    # dedicated element (`.m-mtk-download__type`) alongside the actual anchor
    # that points at the ZIP file, so we key off that structure instead of the
    # anchor text itself.
    for download_block in soup.select(".m-mtk-download"):
        type_element = download_block.select_one(".m-mtk-download__type")
        if not type_element:
            continue
        label = " ".join(type_element.get_text(" ", strip=True).split()).lower()
        if not label.startswith("full multitrack"):
            continue

        anchor = download_block.select_one(".m-mtk-download__links a[href]")
        if not anchor:
            anchor = download_block.find("a", href=True)
        if not anchor:
            continue

        href = anchor.get("href")
        if not href:
            continue

        full_url = urljoin(base_url, href)
        filename = infer_filename(full_url)
        if not filename:
            continue

        links.setdefault(filename, full_url)

    return links


def infer_filename(url: str) -> str | None:
    parsed = urlparse(url)
    path = parsed.path
    if not path:
        return None
    filename = Path(path).name
    if not filename:
        return None
    if "?" in filename:
        filename = filename.split("?")[0]
    return filename


def main() -> int:
    args = parse_args()
    ensure_directory(args.target_dir)

    cookie_value = args.cookie
    if args.cookie_file:
        try:
            cookie_value = args.cookie_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            console.print(
                Panel.fit(
                    f"Failed to read cookie file:\n[red]{exc}[/]",
                    title="Error",
                    border_style="red",
                )
            )
            return 1

    if cookie_value is None:
        entered_cookie = prompt_for_value("Cookie header (press Enter to skip): ")
        if entered_cookie:
            cookie_value = entered_cookie
    if cookie_value == "":
        cookie_value = None

    user_agent = args.user_agent or ""
    if not user_agent:
        entered_agent = prompt_for_value("User-Agent (press Enter to use default): ")
        if entered_agent:
            user_agent = entered_agent
    if not user_agent:
        user_agent = USER_AGENT

    base_headers = build_base_headers(user_agent, args.index_url)

    session = requests.Session()

    try:
        index_html = load_index(
            session,
            args.index_url,
            cookie=cookie_value,
            base_headers=base_headers,
        )
    except Exception as exc:  # noqa: BLE001 - surface helpful context to caller
        console.print(
            Panel.fit(
                f"Failed to load index page:\n[red]{exc}[/]",
                title="Error",
                border_style="red",
            )
        )
        return 1

    if args.dump_index:
        dump_path = args.dump_index
        try:
            if dump_path.parent and dump_path.parent != dump_path:
                ensure_directory(dump_path.parent)
            dump_path.write_text(index_html, encoding="utf-8")
            console.print(
                Panel.fit(
                    f"Saved fetched HTML to [italic]{dump_path}[/].",
                    title="Index Dump",
                    border_style="cyan",
                )
            )
        except OSError as exc:
            console.print(
                Panel.fit(
                    f"Failed to write index dump:\n[red]{exc}[/]",
                    title="Index Dump Error",
                    border_style="red",
                )
            )

    link_map = iterate_full_multitrack_links(index_html, args.index_url)
    if not link_map:
        console.print(
            Panel.fit(
                "No \"Full Multitrack\" links found. The page structure may have changed.",
                title="No Downloads Found",
                border_style="red",
            )
        )
        return 1

    existing_dirs = [args.target_dir]
    parent_dir = args.target_dir.parent
    if parent_dir and parent_dir != args.target_dir:
        existing_dirs.append(parent_dir)

    existing_files = gather_existing_files(existing_dirs)
    catalog_filenames = {name.lower() for name in link_map.keys()}
    present_count = sum(1 for name in catalog_filenames if name in existing_files)

    to_process: list[tuple[str, str]] = []
    for filename, url in link_map.items():
        key = filename.lower()
        if key in existing_files and not args.overwrite:
            continue
        to_process.append((filename, url))

    missing_count = len(to_process)

    render_overview(
        "Cambridge MT",
        total_links=len(link_map),
        existing_count=present_count,
        missing_count=missing_count,
        target_dir=args.target_dir,
    )
    render_remaining_counter(missing_count)

    if not to_process:
        return 0

    console.print(build_table(to_process, "Missing Full Multitrack Downloads"))

    if args.dry_run:
        console.print(
            Panel.fit(
                "Dry run enabled — no files will be downloaded.",
                title="Dry Run",
                border_style="yellow",
            )
        )
        return 0

    if args.overwrite:
        console.print(
            Panel.fit(
                "Overwrite mode active — existing files will be replaced.",
                title="Overwrite",
                border_style="bright_magenta",
            )
        )

    results: list[tuple[str, str, str]] = []
    total_bytes = 0
    downloaded_count = 0
    failed = False

    with Progress(*PROGRESS_COLUMNS, console=console, transient=True) as progress:
        for index, (filename, url) in enumerate(to_process):
            destination = args.target_dir / filename
            request_headers = dict(base_headers)
            request_headers["Referer"] = args.index_url
            if cookie_value:
                request_headers["Cookie"] = cookie_value
            try:
                updated, bytes_written = download_with_progress(
                    session,
                    url,
                    destination,
                    overwrite=args.overwrite,
                    progress=progress,
                    headers=request_headers,
                )
            except Exception as exc:  # noqa: BLE001
                results.append((filename, "[red]Failed[/]", str(exc)))
                failed = True
                continue

            if updated:
                downloaded_count += 1
                total_bytes += bytes_written
                results.append((filename, "[green]Downloaded[/]", str(destination)))
            else:
                results.append((filename, "[yellow]Skipped[/]", str(destination)))

            if args.delay > 0 and index < len(to_process) - 1:
                time.sleep(args.delay)

    console.print(build_summary_table(results))

    console.print(
        Panel.fit(
            (
                f"Fetched [bold]{downloaded_count}[/] archive(s).\n"
                f"Transferred [bold]{format_bytes(total_bytes)}[/]."
            ),
            title="Session Complete" if not failed else "Session Finished",
            border_style="green" if not failed else "yellow",
        )
    )

    remaining_after = sum(1 for _, status, _ in results if "[red]Failed" in status)
    render_remaining_counter(remaining_after)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
