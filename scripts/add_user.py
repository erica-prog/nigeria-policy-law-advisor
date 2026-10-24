"""Admin-provisioned account creation - the only way to create a lawyer's
account (the web API has no sign-up endpoint yet).

Usage:
  uv run python -m scripts.add_user <username> "<Display Name>"

Prompts for the password interactively so it never ends up in shell
history or process listings.
"""

import argparse
import getpass

from policy_advisor.api.credentials import hash_password, load_credentials, save_credentials


def create_account(username: str, display_name: str, password: str) -> None:
    """Add one account to the credential store, or exit if it exists."""
    if username in load_credentials()["usernames"]:
        raise SystemExit(f"User '{username}' already exists.")
    if not password:
        raise SystemExit("Password cannot be empty.")
    save_credentials(
        {
            "usernames": {
                username: {"name": display_name, "password": hash_password(password), "email": ""}
            }
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("display_name")
    args = parser.parse_args()

    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise SystemExit("Passwords did not match.")

    create_account(args.username, args.display_name, password)
    print(f"Created account '{args.username}' ({args.display_name}).")


if __name__ == "__main__":
    main()
