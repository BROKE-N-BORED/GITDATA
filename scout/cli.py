"""Dataset Scout command line (thin wrapper over the core).

    scout-cli search <query> [--category reasoning] [--limit 30]
    scout-cli sources list
    scout-cli sources add <name> <url-template>
    scout-cli sources remove <name>
    scout-cli sources defaults
    scout-cli list
    scout-cli add <url> [--title T] [--category C] [--note N]
    scout-cli remove <url>
    scout-cli check
    scout-cli import <file>
    scout-cli export <format> <file>     # format: md, json, stella
    scout-cli update-check
    scout-cli --self-test
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import apis, core, selftest


def _cmd_search(args: argparse.Namespace) -> int:
    # GitHub takes at most 100 results per page (v0.1.3 T43).
    limit = max(1, min(args.limit, 100))
    query = core.build_query(args.query, args.category)
    print(f"Searching for: {query}")
    results: list[dict] = []
    failures: list[tuple[str, str]] = []
    try:
        results = apis.search_github(query, args.category,
                                     token=core.get_github_token(),
                                     per_page=limit)
    except Exception as exc:  # noqa: BLE001 - one dead source must not kill the search
        core.log_error(f"CLI search failed: {exc}")
        failures.append(("GitHub", str(exc)))
    try:
        results += apis.search_huggingface(query, args.category,
                                           limit=limit)
    except Exception as exc:  # noqa: BLE001 - one dead source must not kill the search
        core.log_error(f"CLI search failed: {exc}")
        failures.append(("Hugging Face", str(exc)))
    if len(failures) == 2:
        # Both sources failed: say whether the internet itself is
        # down (T67) instead of blaming the search.
        if not apis.is_online():
            print("You are offline. Check the connection and try "
                  "again.", file=sys.stderr)
            return 1
        detail = "; ".join(f"{name}: {reason}"
                           for name, reason in failures)
        print(f"Search failed. {detail}", file=sys.stderr)
        return 1
    found = len(results)
    results = core.filter_results(
        results,
        active_only=not args.include_inactive,
        months=None if args.include_inactive else core.DEFAULT_ACTIVE_MONTHS,
        min_stars=args.min_stars,
        license_filter=args.license or "",
    )
    hidden = found - len(results)
    # Newest first, like the GUI (v0.1.3 T32/T37).
    results.sort(key=core.updated_sort_key, reverse=True)
    if failures:
        print(f"Note: {failures[0][0]} search did not answer.",
              file=sys.stderr)
    if not results:
        if hidden:
            print(f"No results. ({hidden} hidden by filters)")
        else:
            print("No results.")
        return 0
    print(f"Found {len(results)} results"
          + (f" ({hidden} hidden by filters)" if hidden else "")
          + ".")
    for item in results:
        print(
            f"- {item['title']} [{item['source']}] "
            f"{item['stars']} stars, updated "
            f"{core.format_date(item['updated'])}"
        )
        print(f"  {item['url']}")
        if item.get("description"):
            print(f"  {item['description']}")
    return 0


def _cmd_sources(args: argparse.Namespace) -> int:
    if args.action == "list":
        for site in core.load_sources():
            print(f"- {site['name']}: {site['url']}")
        return 0
    if args.action == "add":
        sites = core.load_sources()
        sites.append({"name": args.name, "url": args.url})
        core.save_sources(sites)
        print(f"Added {args.name}.")
        return 0
    if args.action == "remove":
        sites = [s for s in core.load_sources() if s["name"] != args.name]
        core.save_sources(sites)
        print(f"Removed {args.name}.")
        return 0
    if args.action == "defaults":
        core.restore_default_sources()
        print("Restored the default websites.")
        return 0
    return 2


def _print_list_notice() -> None:
    """Say what went wrong loading the list, if anything did (T4)."""
    notice = core.list_load_notice()
    if notice:
        print(notice)


def _cmd_list(_args: argparse.Namespace) -> int:
    items = core.load_list()
    _print_list_notice()
    if not items:
        print("My List is empty.")
        return 0
    for item in items:
        state = core.WORKFLOW_STATES.get(item.get("state") or "found",
                                         "Found")
        print(f"- {item['title']} [{item['category']}] [{state}]")
        print(f"  {item['url']}")
    return 0


def _cmd_add(args: argparse.Namespace) -> int:
    url = (args.url or "").strip()
    if not url:
        print("Give a link first.", file=sys.stderr)
        return 1
    if "://" not in url:
        url = "https://" + url
    item = {
        "title": (args.title or "").strip() or url,
        "url": url,
        "category": args.category,
        "source": "Found by hand",
        "stars": 0,
        "updated": "",
        "license": "",
        "notes": (args.note or "").strip(),
    }
    items = core.load_list()
    items, added = core.add_to_list(items, item)
    if not added:
        print("That link is already in My List.")
        return 0
    core.save_list(items)
    print(f"Added {item['title']} to My List.")
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    items = core.load_list()
    before = len(items)
    items = core.remove_from_list(items, args.url)
    if len(items) == before:
        print("That link is not in My List.")
        return 0
    core.save_list(items)
    print("Removed from My List.")
    return 0


def _cmd_check(_args: argparse.Namespace) -> int:
    items = core.load_list()
    _print_list_notice()
    if not items:
        print("My List is empty. Nothing to check.")
        return 0
    counts = {"ok": 0, "dead": 0, "unknown": 0}
    for item in items:
        try:
            status = apis.check_link(item["url"])
        except Exception:  # noqa: BLE001 - one bad link must not kill the run
            status = "unknown"
        item["status"] = status
        counts[status] = counts.get(status, 0) + 1
        print(f"- {item['title']}: {status}")
    core.save_list(items)
    print(f"{counts.get('ok', 0)} working, {counts.get('dead', 0)} dead, "
          f"{counts.get('unknown', 0)} unknown.")
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    try:
        # utf-8-sig strips a byte-order mark (v0.1.3 T35).
        with open(args.file, encoding="utf-8-sig") as handle:
            text = handle.read()
    except OSError as exc:
        print(f"Could not read {args.file}: {exc}", file=sys.stderr)
        return 1
    try:
        found = core.import_file_text(text)
    except ValueError as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1
    items = core.load_list()
    added = 0
    for item in found:
        items, was_added = core.add_to_list(items, item)
        added += 1 if was_added else 0
    core.save_list(items)
    print(f"Imported {added} links "
          f"({len(found) - added} already in the list).")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    items = core.load_list()
    if args.format == "md":
        text = core.export_markdown(items)
    elif args.format == "json":
        text = core.export_json(items)
    else:
        text = core.export_stella_manifest(items)
    try:
        # Atomic (v0.1.3 T21): a crash mid-write never leaves a
        # half-written export behind.
        core.write_text_atomic(Path(args.file), text)
    except OSError as exc:
        print(f"Could not write {args.file}: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {len(items)} items to {args.file}.")
    return 0


def _cmd_update_check(_args: argparse.Namespace) -> int:
    try:
        tag = apis.latest_release_tag()
    except Exception:  # noqa: BLE001 - a rate limit must not crash the check
        tag = None
    if not tag:
        print("Could not reach GitHub to check for updates.")
        return 1
    if core.parse_version(tag) <= core.parse_version(core.VERSION):
        print(f"Dataset Scout {core.VERSION} is the latest version.")
    else:
        print(f"Version {tag} is out. You have {core.VERSION}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scout-cli",
        description="Dataset Scout: find training datasets for STELLA.",
    )
    parser.add_argument(
        "--self-test", action="store_true",
        help="Check search, sniff, and conversion end to end, "
             "then report pass or fail.")
    sub = parser.add_subparsers(dest="command", required=False)

    search = sub.add_parser("search", help="Search GitHub and Hugging Face.")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--category", default="all",
                        choices=list(core.CATEGORIES))
    search.add_argument("--limit", type=int, default=30,
                        help="Results per website (1 to 100).")
    search.add_argument("--min-stars", type=int, default=0)
    search.add_argument("--license", default="",
                        help="Keep only results with this license.")
    search.add_argument("--include-inactive", action="store_true",
                        help="Include results not updated recently.")
    search.set_defaults(func=_cmd_search)

    sources = sub.add_parser("sources", help="Manage custom websites.")
    sources.add_argument("action", choices=["list", "add", "remove", "defaults"])
    sources.add_argument("name", nargs="?", default="")
    sources.add_argument("url", nargs="?", default="")
    sources.set_defaults(func=_cmd_sources)

    lst = sub.add_parser("list", help="Show My List.")
    lst.set_defaults(func=_cmd_list)

    add = sub.add_parser("add", help="Add a link to My List.")
    add.add_argument("url")
    add.add_argument("--title", default="")
    add.add_argument("--category", default="all")
    add.add_argument("--note", default="")
    add.set_defaults(func=_cmd_add)

    remove = sub.add_parser("remove", help="Remove a link from My List.")
    remove.add_argument("url")
    remove.set_defaults(func=_cmd_remove)

    check = sub.add_parser("check", help="Check every link in My List.")
    check.set_defaults(func=_cmd_check)

    imp = sub.add_parser("import",
                         help="Import a Markdown, JSON, or STELLA export.")
    imp.add_argument("file")
    imp.set_defaults(func=_cmd_import)

    export = sub.add_parser("export", help="Export My List to a file.")
    export.add_argument("format", choices=["md", "json", "stella"])
    export.add_argument("file")
    export.set_defaults(func=_cmd_export)

    update = sub.add_parser("update-check",
                            help="Say whether a newer version is out.")
    update.set_defaults(func=_cmd_update_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.self_test:
        checks = selftest.run_checks()
        print(selftest.format_report(checks))
        return 0 if all(check["ok"] for check in checks) else 1
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
