"""Administer the source registry (docs/24-source-registry.md).

The registry decides which external sources the advisor may draw on. There
is no admin page for it: a lawyer's sign-off is recorded from this command
line, which keeps the decision out of the browser client and in the hands of
whoever runs the server.

Usage:
  uv run python -m scripts.manage_sources seed [--file sources.json]
  uv run python -m scripts.manage_sources list [--country NG] [--allowed | --not-allowed]
  uv run python -m scripts.manage_sources show <source_id>
  uv run python -m scripts.manage_sources allow <source_id> --by "<name>"
  uv run python -m scripts.manage_sources disallow <source_id> --by "<name>"

`seed` inserts the sources shipped with the package (or those in --file) and
refreshes the descriptive columns of rows that already exist. It never
changes a row's sign-off: that is what `allow` and `disallow` are for.
"""

import argparse
import json
import sys
from dataclasses import asdict

from policy_advisor import sources


def _print_table(rows: list[sources.Source]) -> None:
    if not rows:
        print("No sources.")
        return
    width = max(len(s.source_id) for s in rows)
    print(f"{'source_id':<{width}}  cc  allowed  connector        trust      name")
    for s in rows:
        flag = "yes" if s.allowed else "no "
        print(
            f"{s.source_id:<{width}}  {s.country:<2}  {flag:<7}  {s.connector:<15} {s.trust_kind:<10} {s.name}"
        )


def cmd_seed(args: argparse.Namespace) -> int:
    report = sources.seed(sources.load_seed(args.file))
    print(f"inserted {len(report.inserted)}: {', '.join(report.inserted) or '-'}")
    print(f"updated  {len(report.updated)}: {', '.join(report.updated) or '-'}")
    not_allowed = [s.source_id for s in sources.list_sources(allowed=False)]
    if not_allowed:
        print(
            f"{len(not_allowed)} source(s) await a lawyer's sign-off; see `manage_sources allow`."
        )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    allowed = True if args.allowed else (False if args.not_allowed else None)
    _print_table(sources.list_sources(country=args.country, allowed=allowed))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    source = sources.get_source(args.source_id)
    if source is None:
        print(f"unknown source {args.source_id!r}", file=sys.stderr)
        return 1
    print(json.dumps(asdict(source), indent=2, default=str))
    return 0


def cmd_set_allowed(args: argparse.Namespace, allowed: bool) -> int:
    try:
        source = sources.set_allowed(args.source_id, allowed, by=args.by)
    except sources.UnknownSource:
        print(f"unknown source {args.source_id!r}; run `seed` first", file=sys.stderr)
        return 1
    state = "allowed" if source.allowed else "not allowed"
    print(f"{source.source_id} is now {state} (by {source.allowed_by}, at {source.allowed_at})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("seed", help="insert or refresh the packaged sources")
    p.add_argument("--file", help="a JSON file in the seed_sources.json shape")
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("list", help="list sources")
    p.add_argument("--country")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--allowed", action="store_true")
    group.add_argument("--not-allowed", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="print one source as JSON")
    p.add_argument("source_id")
    p.set_defaults(func=cmd_show)

    for name, value in (("allow", True), ("disallow", False)):
        p = sub.add_parser(name, help=f"record that a lawyer {name}s a source")
        p.add_argument("source_id")
        p.add_argument("--by", required=True, help="who made the decision")
        p.set_defaults(func=lambda a, v=value: cmd_set_allowed(a, v))

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
