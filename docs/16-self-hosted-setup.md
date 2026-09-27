# 16 — Self-Hosted Setup: One Free Server, One Database File

How to run the advisor for users anywhere, including across Africa, without a
paid database plan. The app and its database run together on one free virtual
machine, and the database is a single SQLite file on that machine's disk.

## Why this shape

Three facts decided it, and they are worth knowing before you start.

**Storage is not where the money goes.** The whole demo corpus - 855 chunks,
their text, and their embeddings - is well under 20 MB. Chat history is five
exchanges per lawyer per matter. Any database would hold this for free. What
actually costs money is Claude: every question is at least one API call, and a
single advisory run is 11-15. Hosting the embedding model is the other real
cost, because it needs 1.5-2 GB of RAM. So the goal is not a cheaper database;
it is not paying for a database service at all.

**No free managed Postgres runs in Africa.** Supabase's regions and Neon's are
all in the Americas, Europe and Asia-Pacific. Supabase's free plan also pauses
a project after a week of low activity until someone restores it by hand, and
keeps no backups.

**For a global app, the database belongs next to the app, not next to the
user.** Answering one question takes several database reads (the vector index,
the keyword index, the chat history) but only one round trip from the lawyer's
browser - and that round trip already waits 3-11 seconds for Claude. An extra
200 ms between a user and a server on another continent is noise. Several
database round trips across a continent, on every question, would not be.
Putting the database inside the app process makes those reads free.

So: one server, one file, no database service, no connection strings, and no
public database endpoint to secure.

| | Cost |
|---|---|
| Server (Oracle Cloud Always Free) | $0 |
| Database (a SQLite file on that server) | $0 |
| Backups (Cloudflare R2 free tier, 10 GB) | $0 |
| HTTPS (Cloudflare Tunnel) | $0 |
| Domain name | optional, roughly $10/year |
| Claude API | pay per use - the only cost that grows with users |

## What you give up

- **One server, one writer.** SQLite serves many simultaneous readers well and
  one writer at a time. For one Streamlit process that is fine. When you need
  several app servers behind a load balancer, move to Postgres; the schema in
  [`src/policy_advisor/migrations/`](../src/policy_advisor/migrations/) is
  plain SQL and ports directly.
- **You run the machine.** Security updates and backups are yours. The steps
  below keep both small.

## Before you start: two decisions

### Where to put the server

This is a latency decision and a legal one.

**Latency.** Oracle's free tier is available in Johannesburg, the only free
option on the continent. That is the natural choice for users in Southern and
Eastern Africa. For West Africa - Nigeria, whose law this corpus covers - a
European region such as London is often just as fast or faster, because many
West African internet routes pass through Europe rather than running overland
to South Africa. Do not guess: before choosing, ask a few intended users to
open a speed-test site pointed at each candidate region and compare.

You must pick carefully, because Oracle only provides Always Free resources in
the **home region** you choose at sign-up, and it cannot be changed later.

**Data residency.** "Africa" is not one jurisdiction. Nigeria's Data Protection
Act 2023, Kenya's Data Protection Act 2019, South Africa's POPIA and Ghana's
Data Protection Act each regulate transfers out of their own country, so a
server in Johannesburg is still an international transfer for a Nigerian
lawyer. No single global deployment keeps every user's data in their own
country. What this design does instead:

- stores as little as possible - five exchanges per lawyer per matter, pruned
  automatically, and only citation locators rather than the passages they
  point to;
- keeps everything on one machine you control, with encrypted off-site
  backups;
- leaves the transfer decision where it belongs. Before real client matters go
  in, have a qualified lawyer confirm the legal basis for where the server
  sits. The public demo corpus raises no such question.

### Which free server

Oracle Cloud's Always Free tier gives up to 2 Arm CPUs and 12 GB of memory,
permanently, plus 200 GB of disk. It is the only free option with enough memory
for the embedding model. Two caveats:

- **Capacity.** Oracle frequently reports "out of host capacity" when creating
  free Arm servers, Johannesburg included. Try each availability domain in your
  region, and retry over a few days. If it never succeeds, a small paid VPS of
  about $5/month with 2 GB of RAM runs this app, and is still a fifth of the
  price of Supabase Pro.
- **Idle reclamation.** Oracle may reclaim an Always Free server that looks
  idle for 7 days, meaning its CPU, network and memory use all stay below 20%.
  A daily ping does not help; it barely moves the CPU measure. What does work
  is honest sizing: give the server only as much memory as the app needs, so
  the loaded embedding model keeps memory use above 20% on its own. **1 CPU and
  6 GB is plenty** and leaves the rest of the allowance free. After the app has
  answered one question, check with `free -m`; the "used" figure should be
  comfortably above a fifth of the total.

## Setting it up

### 1. Create the server

1. Sign up at [cloud.oracle.com](https://cloud.oracle.com) and choose your home
   region (see above).
2. Create a compute instance: image **Ubuntu 24.04**, shape
   **VM.Standard.A1.Flex**, **1 OCPU and 6 GB memory**.
3. Save the SSH private key it offers you. You cannot download it again.
4. Leave inbound ports closed apart from SSH. Step 4 publishes the app without
   opening any.

### 2. Install and configure the app

```bash
ssh ubuntu@<server-ip>
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker ubuntu && exit     # log out and back in to apply
git clone https://github.com/erica-prog/nigeria-policy-law-advisor.git
cd nigeria-policy-law-advisor
cp .env.example .env
nano .env    # set ANTHROPIC_API_KEY and AUTH_COOKIE_KEY
```

`DATABASE_PATH` defaults to `data/advisor.db`, inside the `data/` directory that
[docker-compose.yml](../docker-compose.yml) mounts from the host. Leave it
there: anything outside that mount disappears when the container is rebuilt.

### 3. Build the index and start

```bash
docker compose build
docker compose run --rm app python -m policy_advisor.ingestion.build_index
docker compose up -d
```

`build_index` creates the database, applies the schema, and loads the demo
corpus. It should report 855 chunks.

If you are moving an existing installation that still has
`data/index/matters/*/chunks.json` files from the old Chroma setup, run the
migration once, after `build_index`:

```bash
docker compose run --rm app python -m scripts.migrate_to_sqlite
```

### 4. Publish it over HTTPS without opening ports

Streamlit carries lawyers' passwords at login, so it must never be served over
plain HTTP. A Cloudflare Tunnel gives you HTTPS, keeps the server's inbound
ports closed, and costs nothing. Follow Cloudflare's "Create a tunnel" guide
and point the tunnel at `http://localhost:8501`.

### 5. Back up the database file

The server has no automatic backups, and a lost disk means every uploaded
matter has to be uploaded again - the original files are never stored. So back
up daily, off the machine, and encrypted.

**Do not simply copy `advisor.db` while the app is running.** In the
write-ahead-log mode this app uses, recent writes live in a separate
`advisor.db-wal` file until they are folded back in, so a plain copy can miss
them or catch the file mid-write. `scripts/backup_db.py` uses SQLite's own
backup API, which produces a consistent snapshot while the app keeps running.

1. Create a free [Cloudflare R2](https://developers.cloudflare.com/r2/) bucket
   (10 GB free, no charge for downloading) and an API token for it.
2. Install `rclone` on the server and configure two remotes: an S3 remote for
   the bucket, and a **crypt** remote wrapping it. The snapshot contains client
   chats and uploaded document text, so it is encrypted before it leaves the
   machine; Cloudflare never sees the contents.
3. Schedule it with `crontab -e`:

```cron
15 2 * * * cd ~/nigeria-policy-law-advisor && docker compose run --rm app python -m scripts.backup_db --out data/backups && rclone move data/backups r2crypt:advisor-backups
```

4. In R2, add a lifecycle rule deleting objects after 30 days, so backups stay
   inside the free tier and old client data does not accumulate.
5. **Restore once, on purpose, before you need to.** An untested backup is a
   hope, not a backup. Download a snapshot, point `DATABASE_PATH` at it on your
   own machine, and check that your matters and recent chats appear.

### 6. Keep it patched

```bash
sudo apt-get install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

To update the app itself: `git pull && docker compose build && docker compose up -d`.
The schema upgrades itself on start - see below.

## How the database works

Everything lives in one file with four tables, defined in
[`0001_init.sql`](../src/policy_advisor/migrations/0001_init.sql):

- **`matters`** - one row per matter and its owner. Replaces the `meta.json`
  files.
- **`chunks`** - each chunk's text, metadata and embedding together in one
  row. Replaces both Chroma and the `chunks.json` files. Because the keyword
  index and the vector index are both built from these same rows, they can no
  longer disagree about what a matter contains - which was a real risk when a
  document had to be deleted from two separate stores.
- **`chat_exchanges`** - the last five question-and-answer exchanges per lawyer
  per matter, with the source and citation-check result each answer carried, so
  a reloaded answer from an official website still looks different from one
  grounded in the lawyer's documents.
- **`index_meta`** - which embedding model produced the stored vectors. If
  `EMBEDDING_MODEL` changes, the app refuses to search or add documents, with an
  error explaining how to rebuild, rather than comparing vectors from two
  different models - which would give plausible-looking, meaningless results.

Schema changes are numbered SQL files in that folder. On start, the app applies
any it has not yet run and records progress in SQLite's `user_version`, so
upgrading is just deploying the new code.

Vector search is exact: each matter's embeddings are loaded into memory and
compared directly. That is fast well beyond this corpus - tens of thousands of
chunks per matter - and, unlike approximate indexes, can never quietly return
fewer results than asked for.
