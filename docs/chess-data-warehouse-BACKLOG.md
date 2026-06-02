# Backlog — Personal Chess Data Warehouse (v1)

**Epic E1: Ship the v1 personal chess data warehouse** — automatically collect my Lichess games and serve my playing stats via API + dashboard, deployed on AWS via IaC/CI, for under $10/mo.

**Prerequisite:** resolve OTD-1 (implementation language) before S2.

---

### S1 — Project foundation *(enabler)*
*As the developer, I have a deployable, automated baseline so all later work ships safely.*
- T1.1 Init repo; add PRD + design docs
- T1.2 Bootstrap Terraform remote state (S3 backend + DynamoDB lock table)
- T1.3 Set up GitHub OIDC → AWS deploy role (no long-lived keys)
- T1.4 CI: `fmt` / `validate` / `plan` on PR
- **Done:** `terraform plan` runs green in CI.

### S2 — Automated, reliable ingestion
*As a player, my games are collected from Lichess nightly without manual effort.*
- T2.1 Terraform: private S3 raw bucket + DynamoDB table
- T2.2 Store Lichess token in SSM Parameter Store
- T2.3 Ingestion Lambda: fetch since cursor → raw NDJSON to S3 → normalize → upsert
- T2.4 Seed cursor to 2026-05-01; advance only on success; idempotent upserts (game id in SK)
- T2.5 429/transient-error handling + overlap buffer
- T2.6 EventBridge nightly schedule + on-failure destination
- **Done:** nightly run ingests new games; reruns never duplicate.

### S3 — Stats API
*As a player, I can retrieve my stats as JSON.*
- T3.1 HTTP API + Stats Lambda (read-only role)
- T3.2 Endpoints: `/stats/summary`, `/stats/openings`, `/stats/ratings`
- T3.3 Query + in-memory aggregation; `from` / `to` / `timeControl` filters
- T3.4 Stage-level throttling
- **Done:** endpoints return correct stats, spot-checked vs Lichess.

### S4 — Dashboard
*As a player, I can see my stats, not just curl them.*
- T4.1 Static site on S3 + CloudFront (private bucket)
- T4.2 Chart rating trend + win-rate breakdowns from the API
- **Done:** dashboard loads and shows current stats.

### S5 — Observability & data safety
*As the operator, I know when ingestion fails and can rebuild derived data.*
- T5.1 Structured logs + `GamesIngested` metric
- T5.2 Ingestion-failure alarm → SNS email
- T5.3 AWS Budgets alert (low threshold)
- T5.4 Rebuild-from-raw mode (no Lichess calls)
- **Done:** failure alarm fires; rebuild reproduces the table.

### S6 — Release readiness *(Complete)*
*As the developer, the project is finished and portfolio-ready.*
- T6.1 Unit tests: field mapping + aggregations (sample-JSON fixtures)
- T6.2 README (deploy + architecture)
- T6.3 30-day unattended run; teardown/redeploy from Terraform; cost check
- **Done:** SLC "Complete" criteria met.
