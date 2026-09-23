"""Admin-provisioned account creation. Lawyers can also self-register in the
UI's "Sign up" tab (see auth.py); this script is the admin-side path for
creating an account without going through that form.

Usage:
  uv run python -m scripts.add_user <username> "<Display Name>"

Prompts for the password interactively so it never ends up in shell
history or process listings.
"""

import argparse
import getpass

import streamlit_authenticator as stauth

from policy_advisor.auth import load_credentials, save_credentials


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("display_name")
    args = parser.parse_args()

    credentials = load_credentials()
    if args.username in credentials["usernames"]:
        raise SystemExit(f"User '{args.username}' already exists.")

    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if not password:
        raise SystemExit("Password cannot be empty.")
    if password != confirm:
        raise SystemExit("Passwords did not match.")

    credentials["usernames"][args.username] = {
        "name": args.display_name,
        "password": stauth.Hasher.hash(password),
        "email": "",
    }

    save_credentials(credentials)
    print(f"Created account '{args.username}' ({args.display_name}).")


if __name__ == "__main__":
    main()
