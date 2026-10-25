"""Admin-provisioned account creation - the only way to create a lawyer's
account (the web API has no sign-up endpoint yet). Accounts live in the
database (`users` table); DATABASE_URL must be set.

Usage:
  uv run python -m scripts.add_user <username> "<Display Name>"

Prompts for the password interactively so it never ends up in shell
history or process listings.
"""

import argparse
import getpass

from policy_advisor import tenancy
from policy_advisor.api.credentials import UserExists, create_user


def create_account(
    username: str,
    display_name: str,
    password: str,
    tenant_id: str = tenancy.DEFAULT_TENANT,
    role: str = "lawyer",
) -> None:
    """Add one account to the database and place it in a tenant (docs/31),
    or exit if it exists."""
    if not password:
        raise SystemExit("Password cannot be empty.")
    if tenant_id not in tenancy.list_tenants():
        raise SystemExit(
            f"Tenant {tenant_id!r} does not exist; create it with scripts.manage_tenants."
        )
    try:
        create_user(username, display_name, password)
    except UserExists as exc:
        raise SystemExit(str(exc)) from None
    tenancy.add_user_to_tenant(username, tenant_id, role)  # type: ignore[arg-type]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("display_name")
    parser.add_argument(
        "--tenant", default=tenancy.DEFAULT_TENANT, help="the firm (see scripts.manage_tenants)"
    )
    parser.add_argument("--role", choices=list(tenancy.ROLES), default="lawyer")
    args = parser.parse_args()

    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise SystemExit("Passwords did not match.")

    create_account(args.username, args.display_name, password, args.tenant, args.role)
    print(
        f"Created account '{args.username}' ({args.display_name}) as {args.role} in {args.tenant}."
    )


if __name__ == "__main__":
    main()
