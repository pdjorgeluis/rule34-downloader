# Rule34 Downloader

Desktop app for bulk-downloading from rule34.xxx by tag. This folder holds the pure, offline core (query builder, file classification and folder layout, credentials/config); the API client, downloads and GUI come in later cards. Pure Python 3.10+ standard library. Run the tests with `python3 -m unittest discover -s tests -v`.

## Usage (command line)

Needs `requests` (`pip install -r requirements.txt`). Credentials come from `R34_USER_ID` + `R34_API_KEY`, the config file, or the legacy `~/.config/r34dl.json`.

    python3 -m rule34_downloader TAGS... [options]

Options: `--exclude TEXT`, `--min-score N`, `-n/--count N` (default 20) or `--all`, `--order newest|oldest`, `--types pictures,gifs,videos`, `-o/--output DIR` (default `./downloads`), `--no-search-folder`, `--workers 1-5`, `--page-size N`, `--safety-exclude`, `--delay S`, `--api-per-hour N` (request budget, default 55), `--give-up-minutes M` (how long to retry an unavailable server, default 30), `--dry-run`.

Files land in `<output>/<search>/<pictures|gifs|videos>/<id>.<ext>`; complete files are skipped on re-runs. Exit codes: 0 ok, 2 usage, 4 no credentials, 5 permanent API error (auth, captcha, client), 6 other error, 7 server unavailable (re-run the same command later to resume), 130 interrupted. Pages and files are retried with backoff (a `Retry-After` header wins); the last line is always `Done: ... retries=N reason=R`. `R34_BACKOFF_BASE` (seconds, default 5) exists for tests/debugging only. `--dry-run` prints the query, types and destination without touching the network, credentials or disk.


## Error layer building blocks (wired into the engine and CLI since Card 3c)

- `errors.py`: `ApiError` > `TemporaryError` (kinds `http`, `timeout`, `connection`, `search_down`, `malformed`; may carry `retry_after`) / `PermanentError` (kinds `auth`, `captcha`, `client`); `ServerUnavailable` for the give-up case. `check_reply(status, body, headers, api_key)` raises the right class (empty body, `[]`, XML are never errors); `classify_exception` maps timeouts/connection failures. All messages are redacted, then cut to 200 characters.
- `backoff.py`: `Backoff` (5, 10, 20, 40 ... capped at 300 s, 20% jitter; a `Retry-After` value wins) and `GiveUpClock` (30-minute window).
- `budget.py`: `RequestBudget` keeps a rolling 55-per-hour count of API requests in `state.json` (`R34_STATE`, else `~/.local/state/rule34-downloader/`), waiting when full.
- `api.py`: `ApiClient` now classifies every reply with `check_reply` (BOM stripped first via `errors.strip_bom`) and maps timeouts/connection failures to `TemporaryError`; `_get` returns a `Reply` (`(status, body)` plus `.headers`).
- `resilience.py`: `ThrottleGate` (one shared pause for the API client and downloads), `Adaptive` (one worker and slower delay after overloads, recovering one level per 600 clean seconds), `ResilientClient` (retries page fetches with backoff or `Retry-After`, honours the request budget, gives up with `ServerUnavailable` after the window, raises `Stopped` when interrupted; `total_count` is a single attempt) and `check_file_reply` (classifies file-download statuses; no download code uses it yet). `Job(..., retries=True)` wires all of it in: page and per-file retries (3 attempts per file) on one shared gate, adaptive workers/delay, the request budget, `unavailable_policy` `stop`/`pause` with `retry_now()`, and the `server_unavailable`, `unavailable_retry`, `permanent_error`, `waiting`, `retry` and `adaptive` events.

## GUI (work in progress)

The window shell (mint theme, logo, Settings dialog, placeholder Download and Help tabs) needs PySide6, kept out of the CLI and engine. Create the venv and run:

    uv venv .venv && uv pip install -r requirements-gui.txt
    .venv/bin/python -m rule34_downloader.gui

Settings stores the user ID and API key in the config file (owner-only); the key is never shown. For a headless check use `QT_QPA_PLATFORM=offscreen .venv/bin/python -m rule34_downloader.gui --smoke` (prints `smoke ok`) or `--screenshot PATH`. The GUI tests are skipped when PySide6 is missing.

## Download tab

The Download tab holds the search form: tags to include and exclude (a space means AND; no ratings, no OR), an optional minimum score, Newest or Oldest, Pictures / GIFs / Videos (each in its own subfolder), a number of files or All, 1 to 5 parallel workers, an optional safety-tag checkbox (off by default), a folder with Browse and New folder buttons, and a live preview of the search and the destination. The form logic lives in `form_model.py` (pure, no Qt) and the widgets in `gui/search_form.py`. Start builds a validated job and begins the download (see below). Your choices (not the tags) are remembered in `ui.json` next to the config file; it never holds your user ID or key.

## Running a download

Pressing Start (it needs a saved account in Settings; otherwise the status bar says so and nothing starts) switches the Download tab to a progress panel: the search and the destination, a progress bar (a busy bar while the total is unknown), counters, a live log, the API requests used this hour, and a banner for waiting, server-down, paused and slowed-down states. Pause, Resume, Retry now and Stop act on the running job; when it ends a plain result line appears (red on an error), Open folder shows the files, and Back to search returns to the form with your values kept. Settings and Back are disabled while a download runs. Closing the window during a download asks first, then stops the job and waits up to 10 seconds. The panel logic lives in `run_model.py` (pure text helpers) and `gui/run_panel.py`; the thread bridge is `gui/runner.py`.
