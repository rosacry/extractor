# Extractor

Utilities for mirroring multitrack practice sessions from two public catalogues:

- **Cambridge MT** – downloads every "Full Multitrack" archive that is not already present locally.
- **TELEFUNKEN Elektroakustik** – grabs the 23 TELEFUNKEN session archives, skipping anything you already have.

Both scripts keep the downloads organised under `data/`, provide a Rich-powered terminal UI, and support interactive prompting for Cloudflare cookies and browser user agents when required.

> ⚠️ These scripts are intended for personal study. Respect each site's terms of use and do not redistribute the audio files.

## Project Layout

```
data/
	cambridge/     # Cambridge MT multitracks land here
	telefunken/    # TELEFUNKEN archives land here
download_full_multitracks.py
download_telefunken_multitracks.py
download_utils.py
requirements.txt
```

## Requirements

- Python 3.10+
- The packages listed in `requirements.txt` (`requests`, `beautifulsoup4`, `rich`, …)

Install the dependencies into a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

## Cambridge MT downloader

The Cambridge script scrapes `https://cambridge-mt.com/ms3/mtk/`, filters for "Full Multitrack" entries, and avoids re-downloading files that already exist in `data/cambridge` or its parent folder.

```bash
python download_full_multitracks.py --dry-run
```

### Common options

- `--target-dir PATH` – change the destination directory (default `data/cambridge`).
- `--delay SECONDS` – pause between downloads (default `1.0`).
- `--dry-run` – preview what would download without touching the network.
- `--overwrite` – re-download even when a file already exists.
- `--dump-index FILE` – save the raw Cambridge catalogue HTML for troubleshooting.
- `--cookie VALUE`, `--cookie-file PATH`, `--user-agent VALUE` – supply browser headers explicitly.

If you omit the cookie or user agent, the script will prompt for them interactively. Press Enter to accept the defaults. When Cloudflare challenges you, open the site in your browser, complete the verification, copy the `Cookie` header, and paste it at the prompt.

## TELEFUNKEN downloader

The TELEFUNKEN script pulls session metadata via the WordPress JSON API, resolves the "DOWNLOAD AUDIO FILES" links on each session page, and stores any missing archives inside `data/telefunken`.

```bash
python download_telefunken_multitracks.py --dry-run
```

### Common options

- `--target-dir PATH` – change where the archives are written (default `data/telefunken`).
- `--delay SECONDS` – pause between files (default `1.0`).
- `--dry-run` / `--overwrite` – behave the same as the Cambridge script.
- `--cookie`, `--cookie-file`, `--user-agent` – optional headers if the site ever starts gating traffic.

As with the Cambridge script, missing cookie/agent values trigger interactive prompts, so you can paste them one at a time.

## Workflow tips

- Start with `--dry-run` to confirm that only the expected files will download.
- Both scripts log a "Remaining Downloads" panel that counts how many archives are left. Completed transfers disappear from the progress view to keep the current download in focus.
- You can safely re-run either script; anything already on disk (unless `--overwrite` is set) will be skipped.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Failed to load index page: 403 Client Error` (Cambridge) | Supply a fresh Cloudflare cookie via the prompt or `--cookie`. |
| Downloads stall at a single entry | Check your network connection and ensure there is disk space in `data/`. |
| Rich progress panel looks empty | Increase the terminal height; the summary table at the end still lists every file processed. |

## Contributing / License

This repository tracks a personal utility; contributions are welcome via pull request. A formal licence has not been chosen yet—treat the code as "all rights reserved" until one is published.
