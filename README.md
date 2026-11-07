# Rule34 Downloader

## What it does

Rule34 Downloader is a desktop app and a command-line tool. It downloads pictures, GIFs and videos from rule34.xxx for the tags you choose.

It downloads only what you ask for, one search at a time. It never runs by itself in the background.

The app is at version 1.0.0. It was developed and tested with Python 3.14. The window needs PySide6-Essentials 6.12.0. The command line needs only `requests`.

## Install on Linux

You can use a packaged build. Nothing is installed system-wide.

Unpack the tarball:

```
tar -xzf rule34-downloader-1.0.0-linux-x86_64.tar.gz
```

The tarball is about 82 MB. The unpacked folder is about 210 MB.

To install it for your user, run the installer that is inside the tarball. Run this from the folder where you unpacked it, with no argument:

```
bash rule34-downloader/install/install.sh
```

This writes under `$PREFIX`, which is `~/.local` by default. It adds a launcher link, a desktop entry and 8 icons. To remove exactly those files again:

```
bash rule34-downloader/install/uninstall.sh
```

Neither script touches your settings or your downloads.

You can also skip the install and run the unpacked program directly:

```
rule34-downloader/rule34-downloader
```

To build the tarball yourself, see `BUILD.md`.

## Run from source

Create a virtual environment and install the window requirements:

```
uv venv .venv && uv pip install -r requirements-gui.txt
```

Start the window:

```
.venv/bin/python -m rule34_downloader.gui
```

## First start

The app needs your site account before it can download. Open Settings (top right) and enter your user ID and API key.

To get a key:

1. Open `https://rule34.xxx/index.php?page=account&s=options` and log in.
2. Find the part called "API Access Credentials".
3. Tick "Generate New Key?" and save the page.
4. Copy your user ID and the new key into Settings.

The key is stored only on this computer, in `config.json`. Only you can read that file. The window never shows the key again. Do not share it.

## Using the window

The Download tab holds the search form:

- Tags to include. A space between tags means AND. A tag with several words uses an underscore, for example `hyuuga_hinata`. OR is not supported. The words `rating:`, `sort:` and `score:` are managed by the app, so do not type them.
- Tags to exclude, in their own box.
- An optional minimum score, and Newest or Oldest.
- File types: Pictures, GIFs, Videos.
- A number of files, or All.
- Parallel workers, from 1 to 100.
- An optional safety checkbox. It adds exclusions for loli, shota, child, toddler and underage. It is off by default.
- A folder, with Browse and New folder buttons.

Files land in `<folder>/<search name>/<pictures|gifs|videos>/<id>.<ext>`. Complete files are skipped on re-runs, so you can repeat a search.

Type your tags, then press Search (or press Enter in the include box). A search uses one API request and downloads nothing. The line under the preview says how many posts were found and how many files will be downloaded. Press Download to start. It stays disabled until a search matches your tags, exclusions, minimum score and safety checkbox; after you change one of those, the line reads Out of date: press Search.

The form switches to the progress panel. It shows the search and the destination, a progress bar, counters, a live log, a line with the number of posts found, and the API requests used this hour. A banner shows waiting, server-down, paused and slowed-down states. Pause, Resume, Retry now and Stop act on the running download. When it ends, Open folder shows the files and Back to search returns to the form with your values kept.

The Help tab explains the same things in plain words, including what each message means.

Closing the window during a download asks first. If you confirm, the app stops the job and waits up to 10 seconds for it to end. Files already saved are kept.

## Command line

The command line needs only `requests`. It reads the same account from the config file, or from `R34_USER_ID` and `R34_API_KEY`.

```
python3 -m rule34_downloader TAGS... [options]
```

Options:

- `-h`, `--help`: show the help and exit.
- `--exclude`: tags to exclude, separated by spaces or commas.
- `--min-score`: only posts with at least this score.
- `-n`, `--count`: how many files to download.
- `--all`: download everything that matches. It cannot be combined with `-n`.
- `--order`: `newest` or `oldest`.
- `--types`: a comma list of `pictures`, `gifs`, `videos`. The default is `pictures`.
- `-o`, `--output`: the folder for the files. The default is `./downloads`.
- `--no-search-folder`: do not create a folder per search.
- `--workers`: how many files download at the same time. The default is 5.
- `--page-size`: how many posts one request asks for. The default is 1000.
- `--safety-exclude`: exclude loli, shota, child, toddler and underage.
- `--delay`: seconds to wait between downloads. The default is 0.5.
- `--api-per-hour`: the API request budget per hour. The default is 55.
- `--give-up-minutes`: give up on an unavailable server after this long. The default is 30.
- `--dry-run`: print the search and the destination, and stop.

Exit codes:

- 0: ok.
- 2: bad arguments.
- 4: missing credentials.
- 5: permanent error.
- 6: unexpected error.
- 7: server unavailable.
- 130: interrupted.

A dry run:

```
python3 -m rule34_downloader hyuuga_hinata --exclude sketch --dry-run
```

A small download:

```
python3 -m rule34_downloader hyuuga_hinata -n 5 --types pictures -o ./downloads
```

## Where your data lives

On Linux:

- `~/.config/rule34-downloader/config.json`: your credentials, readable only by you.
- `~/.config/rule34-downloader/ui.json`: your form preferences.
- `~/.local/state/rule34-downloader/state.json`: the request-budget log.

On Windows:

- `%APPDATA%\Rule34Downloader\config.json`: your credentials.
- `%APPDATA%\Rule34Downloader\ui.json`: your form preferences.
- `%LOCALAPPDATA%\Rule34Downloader\state.json`: the request-budget log.

You can move the files with `R34_CONFIG` (the config file) and `R34_STATE` (the state file). The credentials can also come from `R34_USER_ID` and `R34_API_KEY`.

## Limits and good behavior

The site allows 60 API requests per hour for each account. The app caps itself at 55.

Each Search uses one request.

One request fetches one page of up to 1000 posts. Downloading the files is not an API request.

When the cap is reached, the app waits and then continues. When the server is busy, the app pauses, uses fewer workers and longer delays, and speeds up again by itself. When the server does not answer, it retries with growing pauses and gives up after the give-up time.

## Troubleshooting

- "No account saved": open Settings and save your user ID and key.
- "Missing authentication": the user ID or the key is wrong. Open Settings and check both. Generate a new key if needed.
- "Server unavailable": the site does not answer. Wait, or press Retry now. On the command line, run the same command again later; complete files are skipped.
- No results for a tag: check the spelling. An unknown tag finds nothing. Use an underscore inside a multi-word tag.
- "That folder path is a file": the folder you chose is a file. Choose another folder.

## Project layout

```
README.md, BUILD.md
requirements.txt, requirements-gui.txt, requirements-build.txt
rule34_downloader/
    api.py, backoff.py, budget.py, cli.py, config.py, engine.py,
    errors.py, files.py, form_model.py, help_content.py, query.py,
    resilience.py, run_model.py, version.py
    gui/        the window (PySide6)
packaging/      icons, PyInstaller spec, Linux install scripts
scripts/        build scripts
tests/          unit tests
```

## Tests

With plain Python, the window tests are skipped:

```
python3 -m unittest discover -s tests
```

With the virtual environment, all tests run:

```
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests
```

## Content notice

The site has adult content. Use this app only if you are of legal age where you live.

The app is for personal use. Respect the site's terms.

The safety box is a filter, not a guarantee. The app never bypasses a captcha.
