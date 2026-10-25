# 31 — Tenancy

**Status:** implemented (docs/30 slice P3-A). **Decisions:** ADR-001 action 16; docs/30
D1 (row-level security in the database) and D2 (three roles, matter sharing).

## What changed

Until this slice, "tenancy" was a filter by matter id in every query, and the
application connected to Postgres as the database owner. A forgotten filter read
every firm's rows, and nothing in the database would have noticed. Now:

- **Firms are rows.** `tenants` and `tenant_users` (with a role: `admin`,
  `lawyer`, `reviewer`). Every existing user was placed in the `default` tenant as a
  lawyer; the reference libraries belong to the `shared` tenant.
- **Every tenant-scoped row says whose it is.** `tenant_id` is NOT NULL on matters,
  chunks, documents, document jobs, conversations, chat exchanges, agent runs and
  matter shares, backfilled from the matter. Its default is the request's tenant, so
  new rows are labelled without every insert naming it.
- **The database enforces isolation.** The application connects as before, then
  switches to the role `policy_advisor_app`, which cannot bypass row-level security.
  Every tenant-scoped table has a forced policy: a request sees its own tenant's rows
  plus the shared libraries, and writes only its own. The tenant is set per transaction
  from a context variable (`tenancy.py`), so one pooled connection serving two firms in
  turn cannot carry one's tenant into the other's transaction.
- **No tenant means nothing.** With the setting unset the policies match only shared
  rows. A script, job or test that forgot the tenant gets an empty result, not another
  firm's data.
- **Roles and sharing.** The owner of a matter may share it with named users of the
  same firm, read-only or writable (`POST /api/matters/{id}/share`). A reviewer can
  read and ask, never upload, delete or create a matter. The web API contract records
  the new fields (`User.tenant_id`, `User.role`, `Matter.shared_with`).

## How the tenant travels

| Where | How it is set |
|---|---|
| A web request | `TenantMiddleware` reads the signed session cookie, whose tenant claim login wrote, and sets the context for the whole request. `current_user` then checks the claim against `tenant_users`: a user moved or removed is logged out at their next request. |
| A chat job thread | `ChatJobTable.submit` copies the request's context into the worker thread. |
| A document job | The job row carries `tenant_id`; `JobTable.run` sets it before ingesting. The standalone worker claims from every firm's queue under the `*` view, which only `policy_advisor.worker` uses; the API process never sets it. |
| Scripts | `build_index` runs as `shared`; `add_user` places the account in a tenant (default `default`); `manage_tenants` creates tenants and moves members. |
| Tests | An autouse fixture runs every test as `default`; `fresh_db` truncates as the owner and recreates the two tenants. |

Why a middleware and not the dependency: synchronous dependencies and handlers run
in worker threads with a copy of the request's context, so a variable set inside a
dependency never reaches the handler. The middleware runs in the request's own
context and everything the request spawns inherits it.

## What is deliberately not here

- **A user in several firms.** `tenant_users` allows it; `membership()` picks the
  first until the product needs a switcher.
- **Writes to the shared libraries are not blocked by the database.** The policy
  accepts a shared row from any tenant because the build script and the tests need to
  write them; the API refuses uploads to libraries at the application level. A later
  hardening can restrict shared writes to an admin role.
- **Residency and retention** are columns on `tenants` with no behaviour yet (slice
  P3-G).
- **The connecting role still bypasses the policies.** That is what lets it run
  migrations and the database check. The setting `DATABASE_APP_ROLE` switches the
  application to the restricted role; leaving it blank is an emergency measure, not a
  configuration.

## Operating it

```bash
uv run python -m scripts.manage_tenants create acme-llp "Acme LLP" --residency ng
```

```bash
uv run python -m scripts.add_user jdoe "Jane Doe" --tenant acme-llp --role lawyer
```

`scripts.check_database` fails when the application role is missing or can bypass
row-level security, or when a tenant-scoped table lacks its forced policy.

On Supabase the `postgres` role creates `policy_advisor_app` and grants itself
membership during migration 0009; the `extensions` schema is granted to the role so
the vector type resolves. Locally, CI and the scratch cluster connect as a superuser,
which the role switch demotes for the life of the connection.

## Tests that prove it

`tests/test_tenancy.py`: two firms' rows on the same pooled connection, a count with
no matter filter that returns only the caller's rows plus the libraries, a write into
another firm refused by the policy, a job that runs as its own tenant, and the role
and sharing rules through the API.
