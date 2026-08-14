# Cloud SQL restore runbook

Use this runbook to prove that a production backup can be recovered without modifying the production instance. Run the drill in `web-analytics-agency-prod`, create a uniquely named temporary target, and delete only that target after validation.

## Guardrails

- Confirm the source backup status is `SUCCESSFUL` before starting.
- Never use `measurement-db` as the restore target.
- Create the target with deletion protection and automated backups disabled, zonal availability, connector enforcement, and encrypted-only transport.
- Keep the target in `us-central1` and do not add authorized networks.
- Do not print database passwords, rows, OAuth material, or client data. Compare counts, migration versions, policy/role presence, timestamps, and deterministic hashes only.
- Keep `measurement-report-dispatch` paused throughout the drill.

## Procedure

1. List the newest automated backups and record the selected backup ID, start time, end time, and status.
2. Create a small temporary PostgreSQL 18 target named `measurement-db-restore-drill-YYYYMMDD`. Standard Cloud SQL backup IDs restore into an existing target, so target creation is a separate managed operation.
3. Restore the selected production backup into the temporary target with `gcloud sql backups restore`, explicitly naming `measurement-db` as the backup instance.
4. Wait for the restore operation to reach `DONE` with no error.
5. Connect through Cloud SQL Auth Proxy. Validate:
   - database `measurement` is reachable;
   - `analytics.report_snapshots` exists;
   - migrations `002_production` through `009_oauth_assignment_management` are present;
   - all 39 tenant RLS policies and the `measurement_admin`, `measurement_ingestion`, and `measurement_tenant` roles exist;
   - organization, company, website, assignment, report-execution, and snapshot counts are plausible;
   - deterministic hashes for `analytics.report_executions` and `analytics.report_snapshots` match the source when the source has not changed since the backup.
6. Stop both proxy processes and delete only the temporary target. Wait for deletion to reach `DONE`, then confirm the instance no longer exists.
7. Record the backup and managed-operation evidence in `PRODUCTION-READINESS.md` and `REQUIREMENTS-AUDIT.md` without recording sensitive row data.

## Last verified drill

On August 13, 2026, automated backup `1786662000000` restored successfully to temporary target `measurement-db-restore-drill-20260813`. Creation operation `a7b5754d-7bb1-4fcf-8192-100300000032`, restore operation `f9de7a9d-bb0d-46fb-9410-950000000032`, and deletion operation `26bccefd-f348-49e2-b94f-9f7d00000032` all completed with status `DONE` and no errors. The temporary instance was deleted after validation.
