# Repository Agent Instructions

## Working Principles

* Inspect before editing. Understand the relevant schema, implementation, callers, consumers, tests, and configuration before making changes.
* Prefer the smallest coherent change that fixes the root cause.
* Preserve existing behavior unless the task explicitly requires changing it.
* Do not invent state, constants, fallbacks, schema semantics, or business rules.
* Treat similarly named fields as distinct until repository evidence proves otherwise.
* Prefer canonical persisted state over boot-time configuration or singleton objects.
* Maintain tenant, company, and website isolation. Never weaken authorization or cross-tenant boundaries.

## Source of Truth

For database-backed behavior, trace important fields and states to their authoritative source before using them.

When changing a field, enum, mapping, API contract, or persistence path, inspect:

* migrations/schema
* producers/writers
* readers/consumers
* seed/bootstrap paths
* relevant tests

If the repository has multiple possible sources of truth, resolve the ambiguity rather than adding another fallback.

## Database Changes

Before modifying the schema:

* determine migration and backfill requirements
* preserve compatibility with existing data where required
* verify fresh-database and existing-database behavior
* avoid storing the same canonical state in multiple places

Do not add columns merely to preserve legacy response shapes without verifying that the state is meaningful and persistent.

## Verification

Verification is part of implementation.

After making changes:

1. Inspect the complete `git diff`.
2. Trace new or changed semantic assumptions back to repository evidence.
3. Run the narrowest relevant tests/checks first.
4. Run the broader relevant repository validation afterward.
5. For bug fixes, add or update regression coverage when practical.
6. Check for unrelated changes, fabricated values, hidden fallbacks, lossy mappings, and duplicate sources of truth.
7. Verify important failure and edge cases, not only the happy path.

Do not claim success unless the relevant validation actually ran and passed.

Report:

* what changed
* what was verified
* exact validation commands and results
* assumptions or unresolved uncertainty

## Safety

* Do not commit, push, merge, deploy, alter production infrastructure, or modify credentials/secrets unless explicitly requested.
* Do not expose secrets in code, logs, diffs, or responses.
* Do not silently discard user changes.
* Stop and ask before making a destructive or difficult-to-reverse change.
* If correctness depends on an unverified architectural or domain assumption, investigate it before editing.

## Final Review

Before finishing, independently challenge the implementation:

* What assumption could still be wrong?
* Could this break an existing caller or workflow?
* Is every returned value backed by an authoritative source?
* Could this expose or mix data across tenants or websites?
* Are missing/error states represented honestly?
* Do the tests prove the intended behavior rather than merely mirror the implementation?

If a material uncertainty remains, report it instead of declaring the task complete.
