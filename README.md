# Rule34 Downloader

Desktop app for bulk-downloading from rule34.xxx by tag. This folder holds the pure, offline core (query builder, file classification and folder layout, credentials/config); the API client, downloads and GUI come in later cards. Pure Python 3.10+ standard library. Run the tests with `python3 -m unittest discover -s tests -v`.

## Usage (command line)

Needs `requests` (`pip install -r requirements.txt`). Credentials come from `R34_USER_ID` + `R34_API_KEY`, the config file, or the legacy `~/.config/r34dl.json`.

    python3 -m rule34_downloader TAGS... [options]

Options: `--exclude TEXT`, `--min-score N`, `-n/--count N` (default 20) or `--all`, `--order newest|oldest`, `--types pictures,gifs,videos`, `-o/--output DIR` (default `./downloads`), `--no-search-folder`, `--workers 1-5`, `--page-size N`, `--safety-exclude`, `--delay S`, `--dry-run`.

Files land in `<output>/<search>/<pictures|gifs|videos>/<id>.<ext>`; complete files are skipped on re-runs. Exit codes: 0 ok, 2 usage, 4 no credentials, 5 API error, 6 other error. `--dry-run` prints the query, types and destination without touching the network, credentials or disk.

