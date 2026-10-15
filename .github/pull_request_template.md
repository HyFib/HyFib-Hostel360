## What and why

<!-- What changes, and the reason (ticket, tender clause, defect). -->

## How it was checked

- [ ] Server tests for the changed module pass (`bench --site <test site> run-tests --module ...`)
- [ ] Portal: `yarn typecheck` and `yarn test --run` (if `frontend/` changed)
- [ ] `ruff check` and `ruff format --check` are clean
- [ ] DocType JSON changed: `bench migrate` ran cleanly and `modified` was bumped
- [ ] Deploy files changed: the checks in `deploy/README.md` §5 for the files touched

## Contract and documentation

- [ ] No DocType, field, status, endpoint or function in `docs/SOLUTION-DESIGN.md` was renamed or removed
- [ ] `docs/` updated where behaviour, screens or operations changed (design, API, manuals, runbook)
- [ ] `CHANGELOG.md` has an entry under *Unreleased*

## Risk

<!-- Migrations, data changes, permissions, payments, messages sent to students or parents. -->
