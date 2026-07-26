# Memory Index

- [Platform test baseline](project_platform_test_baseline.md) — ~18 pytest failures are pre-existing (no engine venv); baseline against main before blaming a PR.
- [Platform tenancy review checklist](project_platform_tenancy_checklist.md) — multi-tenant isolation model, FK-less tenant_id, X-Portfolio-Id pitfalls, how to hand-test a migration.
- [tlh_plan payload traps](project_tlh_plan_payload_traps.md) — `buckets[cls]` is absent not zero, and `.ir` is post-harvest not owed-today; check any surface printing "IR devido".
