# Technical Design Document — Personal Chess Data Warehouse (v1)

**Status:** Draft v1
**Owner:** [You]
**Last updated:** 2026-05-29
**Companion doc:** `chess-data-warehouse-PRD.md` (requirements; this doc covers *how*)

---

## 1. Overview and Scope

This document specifies the v1 implementation of the personal chess data warehouse described in the PRD. It commits to concrete AWS services, a data model, component logic, and the build sequence. It assumes the PRD's resolved decisions (Lichess-only, nightly sync, backfill from 2026-05-01, token auth, aggregate-on-read, lightly-protected API, AWS serverless).

Where the PRD said *what*, this says *how*. Anything not needed for v1 is called out as a growth path rather than designed here.

---

## 2. Architecture

Two independent paths share one data store. The write path (ingestion) is scheduled and batch; the read path (serving) is on-demand.

```
                          WRITE PATH (scheduled, nightly)
  EventBridge Scheduler ──▶ Ingestion Lambda ──┬─▶ S3  (raw NDJSON, source of truth)
         (cron)                                └─▶ DynamoDB (normalized games + sync cursor)
                                                        │
  ────────────────────────────────────────────────────┼──────────────────────────────────
                                                        ▼   READ PATH (on-demand)
  Browser / client ──▶ CloudFront ──▶ S3 (static dashboard)
                  └──▶ API Gateway (HTTP API, throttled) ──▶ Stats Lambda ──▶ DynamoDB (read)

  Secrets Manager / SSM ──▶ (Lichess token, read by Ingestion Lambda)
  CloudWatch ──▶ logs, metrics, ingestion-failure alarm ──▶ SNS (email)
```

| Component | Service | Role |
|-----------|---------|------|
| Scheduler | EventBridge Scheduler | Triggers ingestion nightly |
| Ingestion | Lambda | Fetch → store raw → normalize → upsert |
| Raw store | S3 | Durable, replayable source of truth |
| Normalized store | DynamoDB (on-demand) | Fast reads for stats |
| Read API | API Gateway HTTP API | Throttled public endpoint |
| Stats compute | Lambda | Query + aggregate-on-read |
| Dashboard | S3 + CloudFront | Static read-only UI |
| Secrets | Secrets Manager or SSM Parameter Store | Holds the Lichess token |
| Observability | CloudWatch + SNS | Logs, metrics, failure alarm |
| IaC | Terraform | All of the above |
| CI/CD | GitHub Actions | Test, plan, deploy |

---

## 3. Data Source: Lichess API

**Endpoint:** `GET https://lichess.org/api/games/user/{username}`, returning newline-delimited JSON (NDJSON), one game object per line.

**Request parameters used:**
- `since` — epoch milliseconds, inclusive. Drives incremental sync.
- `until` — epoch milliseconds, exclusive (optional; used to bound a run if needed).
- `opening=true` — include opening name + ECO code (required for the opening stat).
- `rated`, `perfType` — available filters; v1 ingests all standard games and filters at read time.
- `pgnInJson` — not needed for v1 (we don't store full move lists yet); omit to keep payloads smaller.

**Headers:**
- `Authorization: Bearer <token>` — the personal access token (no special scopes needed for public game export).
- `Accept: application/x-ndjson`
- `User-Agent: chess-data-warehouse/1.0 (<contact>)` — descriptive, per Lichess etiquette.

**Relevant fields per game object** (confirmed against a live sample):
```
id, rated, variant, speed, perf, createdAt, lastMoveAt, status, winner,
players.white.user.name, players.white.rating, players.white.ratingDiff,
players.black.user.name, players.black.rating, players.black.ratingDiff,
opening.eco, opening.name, opening.ply, clock.initial, clock.increment
```
- **Color**: whichever of `players.white.user.name` / `players.black.user.name` equals our username.
- **Result (our perspective)**: `winner == ourColor` → win; `winner` absent → draw; else → loss.
- **Time control category**: the `speed` field directly (`bullet`/`blitz`/`rapid`/`classical`/etc.) — no need to derive it from `clock`.
- **Our rating that game**: `players.<ourColor>.rating`.

**Rate-limit rules** (per Lichess): one request at a time; on HTTP 429, pause ≥60s before resuming. At our volume (backfill from 2026-05-01 + nightly increments) we are very unlikely to hit limits, but the handling is built regardless.

---

## 4. Data Model

### 4.1 S3 — raw store (source of truth)

Raw NDJSON responses are written verbatim, partitioned by date (Hive-style, so a future Athena layer can read it without restructuring):

```
s3://<bucket>/raw/lichess/<username>/dt=YYYY-MM-DD/run-<epochMillis>.ndjson
```

Raw objects are immutable and never deleted in v1. Because they are authoritative, the entire DynamoDB table can be rebuilt from them (FR-15). Lifecycle policies (e.g. transition to cheaper storage) are a future cost optimization.

### 4.2 DynamoDB — normalized store

**Single table, on-demand capacity.** Keying is user-scoped so the multi-user growth path is trivial, and games sort chronologically so date-range queries need no secondary index.

| Item | PK | SK | Key attributes |
|------|----|----|----------------|
| Game | `USER#<username>` | `GAME#<lastMoveAt_ms>#<gameId>` | color, result, speed, eco, openingName, ourRating, oppRating, rated, variant, status, createdAt, lastMoveAt |
| Sync cursor | `USER#<username>` | `META#SYNC` | lastSyncedAt (epoch ms), lastRunAt, lastRunGameCount, lastRunStatus |

**Why this shape:**
- **One Query retrieves all of a user's games**, sorted by time (`PK = USER#<username>`, SK `begins_with GAME#`). Date-range scoping is the same Query with an SK `BETWEEN` range. No scans, no GSIs.
- **Idempotency (FR-6)**: the game ID is in the SK, so re-ingesting the same game `PutItem`s to the same key and overwrites rather than duplicating. Overlapping sync windows are therefore safe.
- **No GSIs in v1**: every stat (by color, opening, time control, rating-over-time) is computed in-memory in the Stats Lambda after the Query. At personal volume this is correct and avoids premature optimization. Precomputed rollups / GSIs are the documented growth path.

**Known limit (acknowledged):** all of one user's games live on a single partition. At personal scale (thousands of games, ~1 KB each) this is comfortably within DynamoDB's 10 GB partition ceiling and fine for throughput. Sharding the partition would only matter at a scale this project will not reach in v1.

---

## 5. Ingestion Design

### 5.1 Run flow

1. **Read cursor** `META#SYNC` from DynamoDB. If absent (first run), seed `lastSyncedAt = 2026-05-01T00:00:00Z` in epoch ms (the configured backfill start, D7).
2. **Fetch token** from Secrets Manager / SSM.
3. **Call Lichess** with `since = lastSyncedAt - overlapBuffer` (a small buffer, e.g. a few minutes, so boundary games are never missed; idempotency dedupes the overlap), plus `opening=true`, NDJSON Accept, auth + User-Agent headers.
4. **Stream the response**; write the raw body to S3 (§4.1).
5. **Parse each line** into a normalized record (§3 field mapping) and accumulate.
6. **Upsert** normalized records via `BatchWriteItem` (idempotent by key).
7. **Advance the cursor** to `max(lastMoveAt)` across ingested games (or leave unchanged if zero games), and record `lastRunAt`, `lastRunGameCount`, `lastRunStatus = success`. **The cursor advances only on a fully successful run.**

### 5.2 Incremental sync and idempotency

`lastSyncedAt` is the high-water mark. Each run fetches only games since that mark (minus the overlap buffer). Because the cursor advances solely on success, a failed run is naturally retried on the next schedule with no gap. Overwriting by `GAME#<lastMoveAt>#<gameId>` makes re-processing harmless.

### 5.3 Error handling

- **429 / transient HTTP errors**: respect the ≥60s pause; abort the run without advancing the cursor (next nightly run resumes). Lambda retries plus an on-failure destination cover unattended recovery.
- **Failure destination / DLQ**: the scheduled async invocation is configured with an on-failure destination (SQS DLQ or SNS) so failures are visible, not silent (supports NFR-9).
- **Partial parse failures**: a malformed line is logged and skipped rather than failing the whole run; raw data is already safe in S3 for later reprocessing.

### 5.4 Rebuild-from-raw (FR-15)

A second entry mode on the ingestion code path (a flag or separate handler) reads all `raw/lichess/<username>/**` objects from S3, re-derives normalized records, and rewrites the DynamoDB table — without calling Lichess. This is what makes raw the source of truth: if stat logic or the schema changes, derived data is disposable and regenerable. Triggered manually (one-off invoke) in v1.

### 5.5 Timeout consideration

Lambda's 15-minute ceiling is ample: the first-run backfill (one person's games since 2026-05-01) and nightly increments are small single-response streams. If the backfill window were ever widened to years of history, ingestion would page across invocations using `until` to bound each chunk — noted as a growth path, not built in v1.

---

## 6. Serving Design (Stats API)

### 6.1 Endpoints

All read-only `GET`, returning JSON, behind the throttled HTTP API.

| Endpoint | Returns |
|----------|---------|
| `GET /stats/summary` | Overall W/L/D + win rate; breakdown by color; breakdown by time control |
| `GET /stats/openings` | Per-opening play counts and results (most-played first) |
| `GET /stats/ratings` | Rating trend over time, as a per-time-control series of points |

**Common query parameters** (all optional): `from`, `to` (ISO dates → SK range), `timeControl` (e.g. `blitz`), `rated` (true/false).

### 6.2 Computation (aggregate-on-read)

Each request: the Stats Lambda issues one DynamoDB Query on `PK = USER#<username>` with an SK range derived from `from`/`to` (or the full range), applies in-memory filters (`timeControl`, `rated`), and folds the games into the requested aggregation. No precomputation, no GSIs — correct for v1 volume.

### 6.3 Example response — `GET /stats/summary`

```json
{
  "user": "<username>",
  "range": { "from": "2026-05-01", "to": "2026-05-29" },
  "totals": { "games": 142, "wins": 71, "losses": 60, "draws": 11, "winRate": 0.500 },
  "byColor": {
    "white": { "games": 73, "wins": 40, "losses": 28, "draws": 5, "winRate": 0.548 },
    "black": { "games": 69, "wins": 31, "losses": 32, "draws": 6, "winRate": 0.449 }
  },
  "byTimeControl": {
    "blitz": { "games": 90, "winRate": 0.511 },
    "rapid": { "games": 52, "winRate": 0.481 }
  }
}
```

---

## 7. API Protection (FR-10a)

Goal: stop an open public endpoint from being hammered — not full user auth.

- **Baseline (recommended): HTTP API stage/route throttling.** API Gateway HTTP API supports request rate + burst limits per stage and per route. This caps abuse (and runaway cost) with zero extra components and is cheaper than a REST API.
- **Optional key:** HTTP API (v2) does **not** support API keys / usage plans natively — that is a REST API (v1) feature. If a shared key is wanted, add a lightweight Lambda authorizer that checks a static key pulled from Secrets Manager. Recommended only if throttling alone proves insufficient.

Decision: ship throttling in v1; treat the key as a fast-follow if needed.

---

## 8. Security and IAM

Least-privilege role per function (NFR-6):

- **Ingestion Lambda role:** read its specific secret (the token); `s3:PutObject` scoped to the `raw/` prefix; `dynamodb:GetItem`/`PutItem`/`BatchWriteItem`/`Query` on the table; write to its log group. Nothing else.
- **Stats Lambda role:** `dynamodb:Query`/`GetItem` on the table (read-only); write to its log group.
- **Scheduler role:** `lambda:InvokeFunction` on the ingestion function only.

Other: no secrets in source (NFR-5) — the token lives in Secrets Manager/SSM and is read at runtime; S3 bucket is private with public access blocked (the dashboard is served via CloudFront, not a public bucket); data handled is public chess data only (NFR-7).

---

## 9. Observability

- **Logs:** structured JSON from both Lambdas (run id, games processed, cursor before/after, errors).
- **Metric:** `GamesIngested` per run (via Embedded Metric Format or `PutMetricData`), plus a `SyncSuccess` signal.
- **Alarm (NFR-9):** ingestion failure → CloudWatch alarm on Lambda `Errors >= 1` over the schedule window (and/or missing `SyncSuccess`) → SNS email. The DLQ/failure destination from §5.3 backs this up.
- **Cost guardrail:** an AWS Budgets alert at a low threshold (e.g. $5) as a backstop against surprises — cheap insurance given the <$10 ceiling.

---

## 10. Infrastructure as Code (Terraform)

- **Remote state:** S3 backend + DynamoDB state-lock table (created via a one-time bootstrap). Demonstrates real team-grade state handling.
- **Layout:** logically grouped configuration — `storage` (S3 + DynamoDB), `ingestion` (Lambda + scheduler + secret wiring), `api` (HTTP API + Stats Lambda + throttling), `frontend` (S3 + CloudFront), `observability` (alarms + SNS + budget), plus shared IAM. Small enough to keep flat-with-clear-files or lightly modularized; avoid over-modularizing at this size.
- **No click-ops (NFR-10):** every resource above is Terraform-managed. Lambda packages are built in CI and referenced by Terraform.

---

## 11. CI/CD (GitHub Actions)

- **Auth:** GitHub OIDC → assume an AWS IAM deploy role. No long-lived AWS keys in GitHub (a strong, employable practice).
- **On pull request:** `terraform fmt -check`, `terraform validate`, `terraform plan`, plus lint and unit tests (field-mapping and aggregation logic are the high-value units to test).
- **On merge to main:** build/package the Lambda artifacts, then `terraform apply`.
- Unit tests should cover the §3 result/color mapping and the §6.2 aggregations using recorded sample game JSON as fixtures.

---

## 12. Cost Model

At personal volume, every component is dominated by free tier or pennies. Indicative monthly:

| Service | Driver | Est. cost |
|---------|--------|-----------|
| Lambda (both) | A handful of invocations/day | ~$0 (free tier) |
| EventBridge Scheduler | ~30 invocations/mo | ~$0 |
| DynamoDB on-demand | Small reads/writes | <$0.25 |
| S3 | A few MB of NDJSON | ~$0 |
| API Gateway HTTP API | Low request volume | ~$0–$0.10 |
| CloudFront + S3 (dashboard) | Tiny static site | ~$0 (free tier) |
| Secrets Manager | 1 secret | ~$0.40 (or $0 with SSM Parameter Store) |
| **Total** | | **well under $1/mo** |

Cheapest variant: use **SSM Parameter Store (SecureString)** instead of Secrets Manager to drop the ~$0.40. The PRD's $10 ceiling has enormous headroom. The only thing that could threaten it — an always-on relational DB — is explicitly avoided.

---

## 13. Scaling and Growth Hooks

Designed-in seams (not built in v1):
- **Chess.com source:** add a second ingester writing the same normalized schema; raw stays partitioned by source (`raw/chesscom/...`).
- **Precomputed rollups / GSIs:** when read latency or volume warrants, materialize aggregates on write.
- **Athena analytics:** the date-partitioned raw S3 layout is already Athena-ready.
- **Multi-user:** the `USER#<username>` partition key already supports it; auth would be the added work.
- **Engine analysis:** a separate queue-based service (PRD project #3) that consumes these games.

---

## 14. Open Technical Decisions

- **OTD-1 (implementation language) — needs your call.** Lambda supports both well. **Python** (boto3, very readable, ubiquitous in data/backend roles) vs **TypeScript/Node** (static types, strong AWS SDK v3, types shared with the dashboard). *Lean:* Python for the backend simplicity unless you specifically want to showcase TypeScript or share types with the frontend. Pick one before §11 test scaffolding.
- **OTD-2 (secrets store):** Secrets Manager vs SSM Parameter Store — functionally equivalent here; SSM is free. *Lean:* SSM Parameter Store for v1.
- **OTD-3 (API key):** ship throttling-only, or add the Lambda-authorizer key now? *Lean:* throttling-only; add the key only if needed (§7).

---

## 15. Build Sequence

Maps to the PRD milestones; each step ends with something verifiable.

1. **Bootstrap:** repo, Terraform remote state, GitHub OIDC deploy role, CI skeleton (`fmt`/`validate`/`plan`).
2. **Storage:** S3 raw bucket (private) + DynamoDB table via Terraform.
3. **Ingestion happy path:** Lambda fetches since the seeded cursor, writes raw to S3, upserts games, advances cursor. Verify with a manual invoke and a real backfill from 2026-05-01.
4. **Ingestion hardening:** incremental cursor, idempotency check, 429/error handling, failure destination, scheduled trigger.
5. **Stats API:** HTTP API + Stats Lambda for the three endpoints, with throttling.
6. **Dashboard:** static page (S3 + CloudFront) charting rating trend + win-rate breakdowns from the API.
7. **Observability + rebuild:** failure alarm, budget alert, and a verified rebuild-from-raw run.
8. **Complete:** README, 30-day unattended run, teardown/redeploy from Terraform, cost check.
