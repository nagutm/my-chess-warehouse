# Product Requirements Document — Personal Chess Data Warehouse (v1)

**Status:** Draft v1.1 (open questions resolved)
**Owner:** [You]
**Last updated:** 2026-05-29
**Project type:** Personal portfolio / skill-building project

---

## 1. Summary

A personal data service that automatically collects my chess games from Lichess, stores them durably, and exposes my playing statistics through an API and a small dashboard. The system runs unattended on a schedule, costs a trivial amount to operate, and is built to demonstrate backend, cloud, and infrastructure competency on AWS.

The product is scoped using the **SLC framework** (Simple, Lovable, Complete): v1 is deliberately small but fully finished and genuinely useful to me as a player, with clear room to grow.

---

## 2. Motivation

**Primary goal (honest framing):** This is a skill-building and portfolio project intended to deepen my backend/cloud/infrastructure abilities and make me more employable. The architecture and engineering practices are as much the "product" as the chess stats themselves.

**Secondary goal (real user value):** As a player, I want to see trends in my own play — where I'm strong, where I leak rating — without manually digging through game history on Lichess. Because the project solves a real (if personal) problem, it has a genuine reason to exist beyond being a demo.

**Why this design demonstrates the target skills:**
- Scheduled, incremental data ingestion (a real ETL pipeline, not a one-off script)
- Separation of a write/ingestion path from a read/serving path
- "Raw is the source of truth, derived data is disposable" data design
- Infrastructure as code and CI/CD as first-class requirements
- Deliberate, cost-aware, scale-to-zero cloud architecture

---

## 3. Goals and Non-Goals

### 3.1 Goals (v1)
- Automatically sync my Lichess games on a schedule with no manual intervention.
- Ingest incrementally (only new games since the last run), not by re-fetching everything.
- Store raw game data durably so derived data can be rebuilt at any time.
- Compute and serve a focused set of personal playing statistics via an API.
- Provide a minimal read-only dashboard so the stats can be *seen*, not just curled.
- Deploy entirely through infrastructure as code and ship via CI/CD.
- Operate for well under the $10/month budget ceiling.

### 3.2 Non-Goals (v1 — explicitly deferred)
- Engine analysis of games (evaluations, blunder detection) — this is a separate future project.
- Multiple data sources (e.g. Chess.com) — Lichess only for v1.
- Multi-user support, accounts, or authentication of end users.
- Real-time or live-game features.
- Precomputed/materialized aggregates — v1 may aggregate on read at personal data volumes.
- SQL-style ad-hoc analytics (Athena/Parquet layer) — a documented growth path, not v1.

---

## 4. Scoping Decisions and Assumptions

These are the calls made while drafting. Each is a candidate for revision — flag any that are wrong.

| # | Decision | Rationale | Easy to change later? |
|---|----------|-----------|----------------------|
| D1 | **Lichess is the only data source in v1** | Single, well-documented free API; keeps scope tight | Yes — Chess.com is a planned growth item |
| D2 | **A minimal static dashboard is in v1 scope** | It's what makes the project "Lovable" vs. a bare API | Yes |
| D3 | **Single user (me); username is configuration** | This is a personal tool | Yes, but multi-user is a larger change |
| D4 | **Read API is lightly protected / throttled in v1** | Data is non-sensitive, but an open endpoint is an abuse/cost surface and a poor portfolio look; throttling is cheap to add | Yes |
| D5 | **AWS serverless, single region, scale-to-zero** | Matches budget and target skill set | Region is config; serverless is a core bet |
| D6 | **Aggregate-on-read for stats in v1** | Personal data volume is small enough; avoids premature optimization | Yes — precomputed rollups are a growth path |
| D7 | **History backfill starts from 2026-05-01** | Bounds first-run volume to a known, modest window; predictable and polite to the Lichess API | Yes — start date is configuration |
| D8 | **Openings are classified from Lichess-provided data** | Lichess already supplies opening/ECO information per game; deriving it independently is unnecessary scope | Yes |
| D9 | **Ingestion authenticates with a Lichess personal access token** | Higher/more forgiving rate limits, documented good practice, and a genuine reason to build real secrets management (see §7.3) | Yes — anonymous access remains technically possible |

---

## 5. Users and Use Cases

**Primary persona:** Me — the developer and the player. I am the only intended user of v1.

**Core use cases:**
1. *As a player*, I open the dashboard and see how I perform by color, by opening, and by time control, and how my rating has trended over time.
2. *As a player*, the system keeps itself up to date with my recent games without me doing anything.
3. *As the operator*, I can confirm the nightly sync ran, see whether it succeeded, and investigate if it didn't.
4. *As the operator*, if my stat definitions change, I can rebuild all derived data from the stored raw games without re-fetching from Lichess.

---

## 6. Functional Requirements

### 6.1 Ingestion
- **FR-1** The system shall fetch my games from Lichess on a recurring schedule (default: nightly).
- **FR-2** The system shall fetch only games newer than the last successful sync (incremental sync via a persisted cursor).
- **FR-3** The system shall persist a "last successfully synced" cursor and advance it only after a successful run. On first run, the cursor is seeded to the configured backfill start date (**2026-05-01**), so the initial sync ingests history from that date forward rather than the entire account history.
- **FR-4** The system shall store the raw fetched data durably and unmodified as the authoritative source of truth.
- **FR-5** The system shall normalize each game into a structured record suitable for fast reads.
- **FR-6** Ingestion shall be idempotent: re-running a sync (including over an overlapping time window) shall not create duplicate records or double-count games. Records are keyed by game ID.
- **FR-7** The system shall behave as a polite API client: identify itself with a descriptive User-Agent and handle rate-limiting (HTTP 429) and transient failures gracefully (e.g. backoff/retry). Per Lichess guidance, requests are made one at a time, and a 429 triggers at least a one-minute pause before resuming.
- **FR-7a** Ingestion shall authenticate to Lichess using a personal access token (no special scopes required for reading public games). The token is supplied to the ingestion function via the secrets mechanism in NFR-5 and never appears in source or logs.

### 6.2 Statistics (Serving)
- **FR-8** The system shall expose an HTTP API that returns my computed statistics as JSON.
- **FR-9** v1 shall compute, at minimum, the following statistics:
  - Overall record (wins / losses / draws) and win rate
  - Win rate by color (White vs. Black)
  - Performance by opening (most-played openings and results within each), using the opening/ECO information Lichess provides per game
  - Performance by time control (bullet / blitz / rapid / classical)
  - Rating trend over time, per time control
- **FR-10** The API shall support a way to scope results (e.g. by time control and/or date range) — exact parameters defined at design time.
- **FR-10a** The API shall be lightly protected against abuse — at minimum request throttling/rate limiting, optionally a simple API key. The goal is to prevent an open public endpoint from being hammered, not to build full user auth (out of scope per §3.2).

### 6.3 Dashboard
- **FR-11** The system shall provide a minimal read-only web page that visualizes the v1 statistics (at least the rating trend as a chart and the win-rate breakdowns).
- **FR-12** The dashboard shall be static (no server-side rendering) and read its data from the stats API.

### 6.4 Operability
- **FR-13** Ingestion runs shall emit logs sufficient to determine success/failure and the number of games processed.
- **FR-14** A failed sync shall be surfaced (e.g. via a metric/alarm) rather than failing silently.
- **FR-15** The system shall support rebuilding all normalized/derived data from the stored raw data without contacting Lichess.

---

## 7. Non-Functional Requirements

### 7.1 Cost
- **NFR-1** Total operating cost shall not exceed **$10/month**; the design target is **under $1/month** at personal data volume.
- **NFR-2** The system shall be scale-to-zero: idle periods incur effectively no compute cost. No always-on instances (explicitly: no RDS/Aurora-style always-on database in v1).

### 7.2 Reliability
- **NFR-3** A missed or failed sync shall be recoverable on the next run (incremental sync resumes from the last good cursor).
- **NFR-4** No data loss: raw game data, once stored, is durable and authoritative.

### 7.3 Security
- **NFR-5** No secrets in source control. The Lichess personal access token (and any other credential) is stored via a managed secrets mechanism (e.g. AWS Secrets Manager or SSM Parameter Store) and read at runtime by the function that needs it.
- **NFR-6** Least-privilege permissions for each component (each function can touch only the resources it needs).
- **NFR-7** Personal data is limited to publicly available chess game data; no sensitive PII is handled.

### 7.4 Observability
- **NFR-8** Logs and basic metrics are available for both the ingestion and serving paths.
- **NFR-9** At least one alarm covers ingestion failure.

### 7.5 Maintainability / Engineering Practices
- **NFR-10** All infrastructure is defined as code (Terraform) — no click-ops.
- **NFR-11** Deployment is automated via CI/CD (GitHub Actions).
- **NFR-12** The codebase includes a README sufficient for a third party to understand and deploy the project (this is a portfolio piece).

---

## 8. High-Level Architecture (Reference)

This is the intended shape, not the detailed design. Two independent paths — ingestion (write) and serving (read) — sharing a data store.

```
Scheduler  →  Ingestion function  →  Object store (raw)  +  NoSQL table (normalized)
                                                                    ↑
              Stats function  ←  HTTP API  ←  Static dashboard / clients
```

**Intended AWS mapping (subject to design phase):**
- Scheduler: EventBridge Scheduler
- Ingestion + stats compute: Lambda
- Raw store: S3
- Normalized store: DynamoDB (on-demand)
- API: API Gateway (HTTP API)
- Dashboard hosting: S3 + CloudFront
- Logs/metrics/alarms: CloudWatch
- IaC: Terraform · CI/CD: GitHub Actions

---

## 9. Success Metrics

**Engineering/portfolio (primary):**
- The system runs unattended on schedule for 30 consecutive days with no manual intervention.
- A full teardown and redeploy from Terraform succeeds from a clean state.
- Derived data can be rebuilt from raw storage and produces identical stats.
- Monthly AWS bill stays under the budget ceiling.

**Product (secondary):**
- All v1 statistics (Section 6.2) are correct when spot-checked against Lichess.
- The dashboard loads and displays current stats.

---

## 10. Out of Scope / Future Growth

Documented to show intentional scoping. None of these block v1.
- **Second data source:** Chess.com ingestion behind the same normalized schema.
- **Precomputed rollups:** materialized aggregates for faster reads as data grows.
- **Analytics layer:** land games as Parquet in S3, query ad hoc with Athena.
- **Engine analysis service:** evaluations and blunder detection (a separate, queue-based project that would consume the games this service collects).
- **End-user auth & multi-user** support.

---

## 11. Resolved Decisions (formerly Open Questions)

All v1 open questions are now resolved; recorded here for traceability.

- **OQ-1 (API exposure) → Resolved:** the v1 read API will be **lightly protected via throttling/rate limiting** (optionally a simple API key). See FR-10a, D4. Not full user auth.
- **OQ-2 (sync frequency) → Resolved:** **nightly** is confirmed. See FR-1.
- **OQ-3 (opening identification) → Resolved:** use the **opening/ECO data Lichess already provides** per game; do not derive independently. See FR-9, D8.
- **OQ-4 (history backfill) → Resolved:** backfill **starts from 2026-05-01**; the sync cursor is seeded to that date on first run. See FR-3, D7.
- **OQ-5 (Lichess token) → Resolved:** **use a personal access token.** Functionally, public game export can be done anonymously, but Lichess gives authenticated requests higher/more forgiving rate limits and recommends using a token. The deciding factor for this project: a token creates a genuine, non-contrived reason to implement end-to-end secrets management (NFR-5/NFR-6) — a directly employable skill — at effectively zero cost or complexity. See FR-7a, D9.

---

## 12. Milestones (Suggested Phasing)

1. **Foundation:** repo, Terraform skeleton, CI/CD pipeline, AWS account/region setup.
2. **Ingestion path:** scheduled fetch → raw store → normalized store, with incremental sync and idempotency.
3. **Serving path:** stats API over the normalized data (Section 6.2 statistics).
4. **Dashboard:** minimal static page consuming the API.
5. **Operability + polish:** logging, failure alarm, README, rebuild-from-raw verification.
6. **Complete:** 30-day unattended run; cost check; teardown/redeploy verification.
