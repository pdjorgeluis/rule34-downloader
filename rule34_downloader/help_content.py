"""Help text for the GUI Help tab. Pure Python, no Qt."""
from __future__ import annotations

import html

OPTIONS_URL = "https://rule34.xxx/index.php?page=account&s=options"

SECTIONS = [
    ("Getting started", """\
This app downloads pictures, GIFs and videos from rule34.xxx for the tags you choose.

- Press Settings (top right) and save your user ID and API key. The next section shows how to get a key.
- On the Download tab, type the tags you want.
- Press Browse... to choose the folder where the files go.
- Press Search. The line under the preview tells you how many posts were found.
- Press Download."""),

    ("Your account and API key", f"""\
The site gives each account a private API key. The app needs your user ID and that key.

- Open this page and log in: {OPTIONS_URL}
- Find the part called API Access Credentials.
- Tick Generate New Key? and save the page.
- Copy your user ID and the new key into Settings in this app.

The key is stored in config.json in the app's config folder. The file can only be read by you. The key is never shown again in the app. Please never share it with anyone."""),

    ("Searching with tags", """\
Type tags in the include box. A space between tags means the posts must have all of the tags. For example, two tags separated by a space find only posts that have both.

A tag with several words is written with an underscore instead of spaces, for example hyuuga_hinata. Commas also split tags.

Searching for one tag OR another is not supported.

To leave posts out, type their tags in the exclude box. Tick Minimum score if you only want posts with at least that score.

The words rating:, sort: and score: are handled by the app (through Newest or Oldest and Minimum score), so you cannot type them as tags.

Press Search (or press Enter in the include box) to count the posts that match your tags. A search uses one API request and downloads nothing. The line under the preview shows how many posts were found and how many files will be downloaded.

Download stays disabled until the count matches your tags. If you change the include or exclude tags, the minimum score or the safety checkbox, the line reads Out of date: press Search. and you press Search again. Changing the order, the file types, the number of files, the workers or the folder does not need a new search."""),

    ("Options", """\
- Newest or Oldest: which posts come first.
- Pictures, GIFs, Videos: tick the kinds of files you want.
- Number of files: how many files to download. Choose All to download everything that matches.
- Parallel workers: how many files download at the same time, from 1 to 100. Fewer workers are gentler on the site.
- The safety checkbox adds exclusions for these tags: loli, shota, child, toddler, underage. It is off by default."""),

    ("Where files are saved", """\
Files go into the folder you chose. If the option for a search folder is ticked, each search gets its own folder named after your tags, and the files are sorted by kind:

<folder>/<search name>/pictures
<folder>/<search name>/gifs
<folder>/<search name>/videos

Files that are already there are not downloaded again, so you can run the same search twice. When a run ends, press Open folder to see the files."""),

    ("While it runs", """\
While a download runs, the app shows progress, counters and a log.

If you pressed Search first, the top of the panel repeats how many posts were found.

- Pause: stops fetching until you press Resume.
- Resume: continues after a pause.
- Stop: ends the download. Files already saved are kept.
- Retry now: if the server is unavailable, tries again right away instead of waiting.
- Back to search: returns to the search form with your values kept. It works when the run has ended.

Settings and Back to search are disabled while a download runs."""),

    ("Rate limits", """\
The site allows 60 API requests per hour for each account. The app keeps itself to 55 per hour to stay safely below that.

Each Search uses one request. A download then uses one more request for every page of up to 1000 posts.

One request fetches one page of up to 1000 posts. Downloading the files themselves does not count as an API request.

The line API requests this hour shows how many you have used. When the limit is reached, the app waits and then continues by itself."""),

    ("Messages and what they mean", """\
Messages above the progress bar:

- Waiting: the app waits a few seconds, for example because of a rate limit.
- Slowed down: the server was busy, so the app uses fewer workers and longer pauses. It speeds up again by itself.
- Server unavailable: the site does not answer. The app retries automatically, or press Retry now.
- Paused: you paused the run. Press Resume.
- Stopping: the run is ending after your Stop.

Messages under the form when something is missing:

- Enter at least one tag.
- Choose at least one file type.
- Workers must be between 1 and 100.
- The number of files must be at least 1.
- Choose a folder.
- That folder path is a file.
- tag in both include and exclude: followed by the tag. Remove it from one of the boxes.

Messages under the form about a search:

- Press Search to count the posts.
- Searching...
- Found N posts. Will download up to N files, newest first.
- Found N posts. Will download every post that matches the file types you ticked, newest first. Only pictures and GIFs are saved, so the number of files can be lower. (The last sentence appears when not all three file types are ticked.)
- Out of date: press Search.
- No posts found. Check the tags.
- The site did not accept your user ID or key. Check Settings.
- The site asked for a captcha. Open the site in your browser, solve it, then try again.
- No requests left this hour. Try again in about N minutes.
- The site did not answer. Try again in a moment.
- The search failed. Try again.

Status bar:

- No account saved - open Settings to add your user ID and API key.
- Account: your user ID and "key saved" mean the account is ready.

When a run ends:

- Finished: shows how many files were downloaded, were already there, were skipped or failed.
- Stopped: the same counts, after you pressed Stop.
- Stopped by an error: the app could not continue. The kind of error follows in brackets, then the message.

Missing authentication means the user ID or the key is wrong. Open Settings and check both."""),

    ("Privacy and safety", """\
Your user ID and key are kept only on this computer, in the config file. The app sends them only to rule34.xxx.

If the site asks for a captcha, the app does not bypass it. It stops with an error instead.

This site has adult content. Use it only if you are of legal age where you live.

The safety checkbox is an optional filter, not a guarantee. Some posts may be tagged badly, so the filter can miss things."""),
]


def _paragraphs(body: str) -> str:
    out = []
    for block in body.strip().split("\n\n"):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        i = 0
        while i < len(lines):
            if lines[i].startswith("- "):
                items = []
                while i < len(lines) and lines[i].startswith("- "):
                    items.append(f"<li>{_inline(lines[i][2:])}</li>")
                    i += 1
                out.append("<ul>" + "".join(items) + "</ul>")
            else:
                text = []
                while i < len(lines) and not lines[i].startswith("- "):
                    text.append(_inline(lines[i]))
                    i += 1
                out.append("<p>" + "<br>".join(text) + "</p>")
    return "\n".join(out)


def _inline(line: str) -> str:
    """Escape a line; turn the options-page URL into a real link."""
    escaped = html.escape(line)
    shown = html.escape(OPTIONS_URL)
    return escaped.replace(shown, f'<a href="{OPTIONS_URL}">{shown}</a>')


def help_html() -> str:
    toc = "".join(
        f'<li><a href="#sec-{n}">{html.escape(title)}</a></li>'
        for n, (title, _body) in enumerate(SECTIONS, 1))
    parts = ["<html><body>", "<h1>Help</h1>", f"<ul>{toc}</ul>"]
    for n, (title, body) in enumerate(SECTIONS, 1):
        parts.append(f'<h2 id="sec-{n}"><a name="sec-{n}"></a>{html.escape(title)}</h2>')
        parts.append(_paragraphs(body))
    parts.append("</body></html>")
    return "\n".join(parts)
