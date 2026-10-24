# Law & Policy Advisor verification map

This directory is the maintained source for verifying the user-facing behavior of the web MVP. Read the index before driving the app, then use the matching feature file as the recipe.

## Baseline preconditions

- Launch with `uv run python .claude/skills/verify-policy-advisor/pa-verify.py launch` and require `pa-verify ready`.
- The only instance this map may drive is `http://127.0.0.1:8765` with data under `.claude/skills/verify-policy-advisor/run/data`.
- Run `uv run python .claude/skills/verify-policy-advisor/pa-verify.py doctor` and require `pa-verify doctor ok` before the first recipe.
- The seeded user is `verify` / `verify-pass-123`, display name `Verify User`.
- Never drive the developer's server on port 8000, and never read or write the repository `data/` tree.

## Driving conventions

- Start every recipe from a freshly launched server unless its preconditions say otherwise.
- Prefer the route the page calls. The commands below are literal.
- Save the session cookie in `.claude/skills/verify-policy-advisor/run/cookies.txt`.
- A browser pass, when you have one, uses the accessible names in each feature file.
- Do not remove `evidence/` during cleanup.

## Proof and skip reporting

- Capture the request and the resulting state, not only the last screen.
- Mutation proof includes a second read: another GET, or the `meta.json` file in the disposable data directory.
- Record the feature id and the entry point with every artifact.
- Report an unreachable path with the command you ran and the unmet precondition. Do not mark a different path as covering it.

## Features

- [Log in](./login.md) covers the login card, a wrong password, and the session that follows a correct one.
- [Start a new case](./start-a-case.md) covers the cases drawer button and the `POST /api/matters` call that actually creates the case.
- [Your cases](./your-cases.md) covers the drawer list, including the shared reference library.
- [Log out](./log-out.md) covers the header button and the cleared session.
- [Add a document](./add-document.md) covers the `+` button and upload. Do not treat it as verified by creating a case.
