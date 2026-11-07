"""Command-line entry point: python3 -m rule34_downloader TAGS..."""
from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

from .config import MissingCredentials, load_credentials, redact
from .query import build_query, normalize_tag, parse_tag_text

KINDS = ("pictures", "gifs", "videos")


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, f"error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="rule34_downloader", description="Bulk-download from rule34.xxx by tag.")
    p.add_argument("tags", nargs="+", help="one or more tags (each word is one tag)")
    p.add_argument("--exclude", default="", help="tags to exclude (space or comma separated)")
    p.add_argument("--min-score", type=int, default=None)
    g = p.add_mutually_exclusive_group()
    g.add_argument("-n", "--count", type=int, default=None)
    g.add_argument("--all", action="store_true", help="download everything")
    p.add_argument("--order", choices=("newest", "oldest"), default="newest")
    p.add_argument("--types", default="pictures", help="comma list of pictures,gifs,videos")
    p.add_argument("-o", "--output", default="./downloads")
    p.add_argument("--no-search-folder", action="store_true")
    p.add_argument("--workers", type=int, default=5)
    p.add_argument("--page-size", type=int, default=1000)
    p.add_argument("--safety-exclude", action="store_true")
    p.add_argument("--delay", type=float, default=0.5)
    p.add_argument("--api-per-hour", type=int, default=55,
                   help="API request budget per hour (default 55)")
    p.add_argument("--give-up-minutes", type=float, default=30.0,
                   help="give up on an unavailable server after this long (default 30)")
    p.add_argument("--dry-run", action="store_true")
    return p


def _parse_types(text: str) -> set:
    types = {t.strip().lower() for t in text.split(",") if t.strip()}
    bad = types - set(KINDS)
    if bad or not types:
        raise ValueError(f"--types must be a comma list of {','.join(KINDS)}")
    return types


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    from .engine import JobConfig  # imports requests lazily; dry-run stays light

    try:
        include = [normalize_tag(t) for t in args.tags]
        exclude = parse_tag_text(args.exclude)
        count = None if args.all else (20 if args.count is None else args.count)
        cfg = JobConfig(
            include=include,
            dest=Path(args.output),
            exclude=exclude,
            min_score=args.min_score,
            order=args.order,
            types=_parse_types(args.types),
            by_search=not args.no_search_folder,
            count=count,
            workers=args.workers,
            page_size=args.page_size,
            safety=args.safety_exclude,
            delay=args.delay,
        )
        query = build_query(cfg.include, cfg.exclude, cfg.min_score, cfg.order, cfg.safety)
        if args.api_per_hour < 1:
            raise ValueError("--api-per-hour must be >= 1")
        if not (math.isfinite(args.give_up_minutes) and args.give_up_minutes > 0):
            raise ValueError("--give-up-minutes must be > 0")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"query: {query}")
        print(f"types: {', '.join(sorted(cfg.types, key=KINDS.index))}")
        print(f"dest: {cfg.dest}")
        return 0

    try:
        creds = load_credentials()
    except MissingCredentials as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4

    raw_base = os.environ.get("R34_BACKOFF_BASE", "5")
    try:
        base = float(raw_base)
        if not (math.isfinite(base) and base > 0):
            raise ValueError
    except ValueError:
        print(f"error: R34_BACKOFF_BASE must be a number > 0 (got {raw_base!r})",
              file=sys.stderr)
        return 2

    from .api import ApiClient
    from .backoff import Backoff, GiveUpClock
    from .budget import RequestBudget
    from .engine import Job

    def on_event(kind, data):
        if kind == "file_done":
            print(f"{data['type']}: {data['id']} ({data['bytes']} bytes)", flush=True)
        elif kind == "file_failed":
            print(f"failed: {data['id']}: {data['error']}", flush=True)
        elif kind == "waiting":
            print(f"waiting: {data['reason']} {data['seconds']:.0f}s "
                  f"(attempt {data['attempt']})", flush=True)
        elif kind == "retry":
            ident = data["pid"] if data["what"] == "page" else data["id"]
            print(f"retry: {data['what']} {ident} attempt {data['attempt']}: "
                  f"{data['error']}", flush=True)
        elif kind == "adaptive":
            print(f"slowing down: level {data['level']}", flush=True)
        elif kind == "server_unavailable":
            print(f"server unavailable: {data['message']}", flush=True)
        elif kind == "permanent_error":
            print(f"permanent error ({data['kind']}): {data['message']}", flush=True)

    job = None
    try:
        cfg.dest.mkdir(parents=True, exist_ok=True)
        job = Job(cfg, ApiClient(creds), on_event=on_event,
                  budget=RequestBudget(limit=args.api_per_hour),
                  giveup=GiveUpClock(window=args.give_up_minutes * 60),
                  backoff=Backoff(base=base), retries=True)
        s = job.run()
    except KeyboardInterrupt:
        if job is not None:
            job.stop()
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"error: {redact(str(exc), creds.api_key)}", file=sys.stderr)
        return 6
    print(
        f"Done: downloaded={s.downloaded} existing={s.existing} skipped_type={s.skipped_type} "
        f"failed={s.failed} pages={s.pages} requests={s.requests_made} "
        f"retries={s.retries} reason={s.reason}",
        flush=True,
    )
    if s.reason in ("count_reached", "end_of_results"):
        return 0
    if s.reason == "permanent_error":
        print(f"error: {s.error_kind}: {redact(s.error or '', creds.api_key)}", file=sys.stderr)
        return 5
    if s.reason == "server_unavailable":
        print("error: server unavailable; re-run the same command later to resume",
              file=sys.stderr)
        return 7
    if s.reason == "stopped":
        return 130
    return 6


if __name__ == "__main__":
    sys.exit(main())
