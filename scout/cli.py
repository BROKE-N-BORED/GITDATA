"""Dataset Scout command line (thin wrapper over the core).

    scout-cli search <query> [--category reasoning] [--limit 30]
    scout-cli sources list
    scout-cli sources add <name> <url-template>
    scout-cli sources remove <name>
    scout-cli sources defaults
    scout-cli list
    scout-cli export <format> <file>     # format: md, json, stella
"""

from __future__ import annotations

import argparse
import sys

from . import apis, core


def _cmd_search(args: argparse.Namespace) -> int:
    query = core.build_query(args.query, args.category)
    print(f"Searching for: {query}")
    try:
        results = apis.search_github(query, args.category,
                                     token=core.get_github_token(),
                                     per_page=args.limit)
        results += apis.search_huggingface(query, args.category,
                                           limit=args.limit)
    except (apis.SearchError, core.RateLimitedError) as exc:
        core.log_error(f"CLI search failed: {exc}")
        print(f"Search failed: {exc}", file=sys.stderr)
        return 1
    results = core.filter_results(
        results,
        active_only=not args.include_inactive,
        months=None if args.include_inactive else core.DEFAULT_ACTIVE_MONTHS,
        min_stars=args.min_stars,
    )
    if not results:
        print("No results.")
        return 0
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


def _cmd_list(_args: argparse.Namespace) -> int:
    items = core.load_list()
    if not items:
        print("My List is empty.")
        return 0
    for item in items:
        print(f"- {item['title']} [{item['category']}]")
        print(f"  {item['url']}")
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
        with open(args.file, "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError as exc:
        print(f"Could not write {args.file}: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {len(items)} items to {args.file}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scout-cli",
        description="Dataset Scout: find training datasets for STELLA.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", help="Search GitHub and Hugging Face.")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--category", default="all",
                        choices=list(core.CATEGORIES))
    search.add_argument("--limit", type=int, default=30)
    search.add_argument("--min-stars", type=int, default=0)
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

    export = sub.add_parser("export", help="Export My List to a file.")
    export.add_argument("format", choices=["md", "json", "stella"])
    export.add_argument("file")
    export.set_defaults(func=_cmd_export)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
