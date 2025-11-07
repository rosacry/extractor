#!/usr/bin/env python3
"""Download TELEFUNKEN multitrack archives.

This script crawls the TELEFUNKEN Elektroakustik multitrack catalogue, extracts
all "Download Audio Files" links from each session page, compares the archive
filenames against what is already stored locally (including sibling folders),
and downloads only the missing sessions. Output is rendered with Rich for a
polished command-line experience.

Requirements:
    pip install -r requirements.txt

Example usage:
    python download_telefunken_multitracks.py --target-dir data/telefunken
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

DEFAULT_INDEX_URL = "https://www.telefunken-elektroakustik.com/multitracks/"
DEFAULT_DELAY = 1.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def build_base_headers(user_agent: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
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
        help="Landing page that lists the TELEFUNKEN sessions",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=Path("data/telefunken"),
        help="Directory where downloads are stored (default: data/telefunken)",
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
    return parser.parse_args()


def fetch_catalog_detail_links(
    session: requests.Session,
    index_url: str,
    base_headers: Mapping[str, str],
    cookie: str | None,
) -> list[str]:
    headers = dict(base_headers)
    if cookie:
        headers["Cookie"] = cookie
    api_url = urljoin(index_url, "/wp-json/wp/v2/multitrack")
    params = {"per_page": 100, "orderby": "date", "order": "desc"}

    try:
        response = session.get(api_url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and data:
            links = [item.get("link", "") for item in data if isinstance(item, dict)]
            cleaned = [link.rstrip("/") for link in links if isinstance(link, str) and link]
            if cleaned:
                return sorted(set(cleaned))
    except Exception:  # noqa: BLE001 - fall back to HTML scraping
        pass

    response = session.get(index_url, headers=headers, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    urls: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        full_url = urljoin(index_url, href)
        normalized = full_url.split("#", 1)[0].rstrip("/")
        if normalized == index_url.rstrip("/"):
            continue
        if "/multitrack/" in normalized:
            urls.add(normalized)
    return sorted(urls)


def extract_download_info(
    session: requests.Session,
    detail_url: str,
    base_headers: Mapping[str, str],
    cookie: str | None,
) -> tuple[str, str]:
    headers = dict(base_headers)
    headers["Referer"] = detail_url
    if cookie:
        headers["Cookie"] = cookie
    response = session.get(detail_url, headers=headers, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    for anchor in soup.find_all("a", href=True):
        text = anchor.get_text(strip=True).lower()
        if "download audio files" in text:
            download_url = urljoin(detail_url, anchor["href"])
            filename = infer_filename(download_url)
            if filename:
                return filename, download_url
    raise RuntimeError(f"No download link found on detail page: {detail_url}")


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

    base_headers = build_base_headers(user_agent)

    session = requests.Session()

    try:
        detail_links = fetch_catalog_detail_links(
            session,
            args.index_url,
            base_headers=base_headers,
            cookie=cookie_value,
        )
    except Exception as exc:  # noqa: BLE001
        console.print(
            Panel.fit(
                f"Failed to enumerate TELEFUNKEN sessions:\n[red]{exc}[/]",
                title="Error",
                border_style="red",
            )
        )
        return 1

    if not detail_links:
        console.print(
            Panel.fit(
                "No session links found on the TELEFUNKEN catalogue page.",
                title="No Downloads Found",
                border_style="red",
            )
        )
        return 1

    entries: list[tuple[str, str, str]] = []
    for link in detail_links:
        try:
            filename, download_url = extract_download_info(
                session,
                link,
                base_headers=base_headers,
                cookie=cookie_value,
            )
        except Exception as exc:  # noqa: BLE001
            console.print(
                Panel.fit(
                    f"Skipping session due to missing download link:\n[red]{link}[/]\nReason: {exc}",
                    title="Warning",
                    border_style="yellow",
                )
            )
            continue
        entries.append((filename, download_url, link))

    if not entries:
        console.print(
            Panel.fit(
                "Unable to locate any \"Download Audio Files\" buttons.",
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
    present_count = sum(1 for filename, _, _ in entries if filename.lower() in existing_files)

    to_process: list[tuple[str, str, str]] = []
    for filename, download_url, detail_url in entries:
        if filename.lower() in existing_files and not args.overwrite:
            continue
        to_process.append((filename, download_url, detail_url))

    missing_count = len(to_process)

    render_overview(
        "TELEFUNKEN",
        total_links=len(entries),
        existing_count=present_count,
        missing_count=missing_count,
        target_dir=args.target_dir,
    )
    render_remaining_counter(missing_count)

    if not to_process:
        return 0

    console.print(
        build_table(
            [(filename, download_url) for filename, download_url, _ in to_process],
            "Missing TELEFUNKEN Downloads",
        )
    )

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
        for index, (filename, download_url, detail_url) in enumerate(to_process):
            destination = args.target_dir / filename
            request_headers = dict(base_headers)
            request_headers["Referer"] = detail_url
            if cookie_value:
                request_headers["Cookie"] = cookie_value
            try:
                updated, bytes_written = download_with_progress(
                    session,
                    download_url,
                    destination,
                    overwrite=args.overwrite,
                    progress=progress,
                    headers=request_headers,
                )
            except Exception as exc:  # noqa: BLE001
                results.append((filename, "[red]Failed[/]", f"{detail_url} | {exc}"))
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
