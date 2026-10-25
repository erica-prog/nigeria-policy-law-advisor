"""Administer firms (tenants) and their members (docs/31-tenancy.md).

Usage:
  uv run python -m scripts.manage_tenants create <tenant_id> "<Name>" [--residency ng|us]
  uv run python -m scripts.manage_tenants list
  uv run python -m scripts.manage_tenants add-user <tenant_id> <username> [--role admin|lawyer|reviewer]

Accounts are created with `scripts.add_user`, which also places the account
in a tenant; this command moves or re-roles existing accounts and creates
tenants. A user is in one tenant today.
"""

import argparse
import sys

from policy_advisor import tenancy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create")
    p.add_argument("tenant_id")
    p.add_argument("name")
    p.add_argument("--residency", choices=["ng", "us"])
    sub.add_parser("list")
    p = sub.add_parser("add-user")
    p.add_argument("tenant_id")
    p.add_argument("username")
    p.add_argument("--role", choices=list(tenancy.ROLES), default="lawyer")
    args = parser.parse_args(argv)

    if args.command == "create":
        try:
            tenancy.create_tenant(args.tenant_id, args.name, residency=args.residency)
        except tenancy.ResidencyNotHonoured as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        print(f"tenant {args.tenant_id} ready")
    elif args.command == "list":
        for tenant in tenancy.list_tenants():
            print(tenant)
    else:
        tenancy.add_user_to_tenant(args.username, args.tenant_id, args.role)
        print(f"{args.username} is a {args.role} in {args.tenant_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
