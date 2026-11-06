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
