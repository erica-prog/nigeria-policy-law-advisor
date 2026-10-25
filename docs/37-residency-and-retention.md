# 37 — Residency, retention and holds

**Status:** implemented (docs/30 slice P3-G). **Decision:** docs/30 D9.

## Residency: a fact, not a feature

A firm's confidentiality duties say where client documents may live. The system cannot
move data between regions; it can only be honest about where its database is. The
deployment states that in `DATABASE_RESIDENCY` (`ng`, `us`, or blank when it is
elsewhere), and a tenant may ask for a residency only when it matches. No hosted
Postgres region is in Nigeria today (docs/16), so an `ng` request is refused unless the
operator has deliberately set it after putting the database there. The refusal names
what the deployment honours, which is the sentence to say to the firm.

`GET /api/tenant` shows the firm its settings and the deployment's residency; `PUT
/api/tenant` (admin role) changes them, and `scripts.manage_tenants create --residency`
goes through the same check.

## Retention: per tenant, per tier, with a floor

`retention_days_matter` and `retention_days_firm` on the tenant. Blank means keep
forever. A value below `RETENTION_MIN_DAYS` (30) is refused, because a short period set
by mistake is an irreversible mistake. The shared libraries belong to the shared tenant,
which has no period and is never a candidate.

A matter is expired when its tenant has a period for its tier and its last activity
(`updated_at`, else `created_at`) is older than that period.

## Holds

`POST /api/matters/{id}/hold` `{ "on": true, "reason": "litigation hold, Suit FHC/L/CS/…" }`
by the owner or a firm admin. A held matter is never deleted by the job, and the job
records that it skipped it. `Matter.hold` and `Matter.hold_reason` show it in the API.

## The job

`policy_advisor/retention.py`. It lists candidates across every firm under the worker's
view, then handles each as its own tenant. By default it is a **dry run**: it records a
`would_delete` event per candidate, with the counts of rows it would remove, and deletes
nothing. With `--apply` it deletes the matter's rows (everything cascades from
`matters`), its originals and artefacts in the blob store, and the local sidecar
directory, and records a `deleted` event with the counts. Held matters get a
`skipped_hold` event. Events are tenant-scoped and readable at
`GET /api/tenant/retention-events` (admin or lawyer).

```bash
uv run python -m scripts.run_retention            # dry run, every firm
```

```bash
uv run python -m scripts.run_retention --apply    # delete
```

The worker runs the job every `RETENTION_RUN_HOURS` (24) as a dry run; it deletes only
when `RETENTION_AUTO_APPLY` is true. The first production run should be the dry run,
read by a person, before the switch is turned.

## Not done here

- Retention for the cost ledger, audit and review events: those are the firm's own
  trail and are kept; a separate policy for them is a later decision.
- Backups: a deleted matter may survive in the provider's point-in-time restore window
  (docs/16) until that window passes. The retention period should be set with that in
  mind, and the firm told.
- Moving a tenant between regions.
