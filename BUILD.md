# Building Rule34 Downloader

## Requirements

- Python 3.14. The project was developed and tested with Python 3.14.
- `uv`, to create the virtual environment.
- PySide6-Essentials 6.12.0 and PyInstaller 6.22.3. Both are pinned in `requirements-build.txt`.
- Linux build: `bash`, `tar`.
- Windows build: PowerShell 5.1 or newer.

## Set up the development environment

From the project folder:

```
uv venv .venv
uv pip install -r requirements-build.txt
```

## Run the tests

With plain Python, the window tests are skipped:

```
python3 -m unittest discover -s tests
```

With the virtual environment, all tests run:

```
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests
```

## Regenerate the icons

The icons are rendered from `rule34_downloader/gui/assets/logo.svg`. Run this only when the logo changed:

```
.venv/bin/python packaging/make_icons.py
```

It writes 8 PNG sizes and the Windows `.ico` under `packaging/icons/`.

## Build for Linux

```
bash scripts/build-linux.sh
```

The script does this:

1. Checks that PyInstaller is available in `.venv` (or in the Python named by `R34_PYTHON`).
2. Builds the onedir app with `packaging/rule34-downloader.spec` into `build/dist/`.
3. Copies the installer files into `build/dist/rule34-downloader/install/`.
4. Smoke-tests the frozen binary with `--smoke`. It must print `smoke ok`.
5. Packs `build/dist/rule34-downloader` into a tarball.
6. Checks the tarball: it unpacks it into a temp directory, runs the packed `install/install.sh` with no argument into a temp `PREFIX` and a temp `HOME`, runs the installed launcher with `--version`, runs the packed `install/uninstall.sh`, and requires that `PREFIX` holds no files.
7. Prints `BUILD OK`.

The last three output lines are:

```
SMOKE OK (frozen binary)
INSTALL CHECK OK
BUILD OK build/rule34-downloader-<version>-linux-<arch>.tar.gz
```

For version 1.0.0 on x86_64 the tarball is `build/rule34-downloader-1.0.0-linux-x86_64.tar.gz`. It is about 82 MB. The unpacked folder is about 210 MB.

Tarball contents: the top folder is `rule34-downloader/`, the PyInstaller app. Inside it, `install/install.sh` and `install/uninstall.sh` install and remove the app, `install/rule34-downloader.desktop` is the desktop entry template, and `install/icons/` holds the 8 PNG icons. Someone who has only the tarball can install it with no source tree.

## Install and uninstall on Linux

Install a built folder under `$PREFIX`. The default is `~/.local`:

```
bash packaging/linux/install.sh build/dist/rule34-downloader
```

To use another place, set `PREFIX` to an absolute path:

```
PREFIX=/tmp/r34-test bash packaging/linux/install.sh build/dist/rule34-downloader
```

The script writes the app folder under `$PREFIX/lib`, a launcher link under `$PREFIX/bin`, a desktop entry under `$PREFIX/share/applications` and 8 icons under `$PREFIX/share/icons`.

To remove exactly those files:

```
PREFIX=/tmp/r34-test bash packaging/linux/uninstall.sh
```

Neither script touches the user's settings or downloads.

## Build for Windows (untested recipe)

This Windows recipe has not been run yet: it was written on Linux and is UNTESTED.

On Windows, create a virtual environment and install the build requirements:

```
py -3.14 -m venv .venv
.venv\Scripts\pip install -r requirements-build.txt
```

Run the build script:

```
powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1
```

The output is `build\rule34-downloader-1.0.0-windows-x86_64.zip`.

The script builds with PyInstaller and then smoke-tests the exe. A windowed Windows build has no standard output, so `--smoke` and `--version` print nothing there. The script therefore runs the exe with `--screenshot` and requires exit code 0 and a non-empty picture file. The last line is `BUILD OK ...`.

In Windows PowerShell 5.1, `Compress-Archive` can fail on very long paths. Build in a short folder path if it does.

### Build it from Linux with GitHub Actions

PyInstaller cannot cross-compile, so the Windows exe is built on a Windows runner. The workflow `.github/workflows/build-windows.yml` sets up Python 3.14, installs `requirements-build.txt`, runs the tests (informational), runs `scripts\build-windows.ps1` (build, `--screenshot` smoke test, zip) and uploads the zip as the artifact `rule34-downloader-windows`, plus `smoke.png` as `smoke-screenshot`.

Start it from the Actions tab (Build Windows > Run workflow) or by pushing a tag `v*`. Download the artifact, unzip it and run `rule34-downloader\rule34-downloader.exe`.

## Release checklist

1. Bump the version in `rule34_downloader/version.py`.
2. Run both test commands from "Run the tests".
3. Regenerate the icons if the logo changed.
4. Build with `bash scripts/build-linux.sh`.
5. Check that the smoke test passed: the output has `SMOKE OK (frozen binary)`.
6. Install into a temporary `PREFIX` and start the program.
7. Update the sizes in `README.md`.
8. Commit.
9. Tag the release.

## Troubleshooting builds

- PyInstaller is missing: the script prints `error: PyInstaller is not available in <python>`. Run `uv pip install -r requirements-build.txt` in the virtual environment.
- Qt plugins are missing, or the logo is not shown: run the frozen program with `--screenshot` and look at the picture.
- `QT_QPA_PLATFORM`: set it to `offscreen` on a machine without a display, for the tests and for the smoke test.
- The build is too large: the spec file lists the excluded Qt modules in `packaging/rule34-downloader.spec`. Add unused modules there.
- Windows has no standard output: the windowed exe prints nothing for `--smoke` and `--version`. Use `--screenshot` instead.
