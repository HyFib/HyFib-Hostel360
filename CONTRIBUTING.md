# Contributing

How the HyFib engineering team changes Hostel360. Setting up a development bench, running
tests and the code conventions are in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Before you start

- **The contract.** `docs/SOLUTION-DESIGN.md` fixes DocType names, fields, statuses, function
  signatures, endpoints and hooks. You may add to it; a rename or removal needs a change to
  the document first, agreed with the technical lead, because other modules, the portal,
  reports and the migration mappings depend on those names.
- **One owner per area.** Each module has an owner; changes to another module's files go
  through its owner's review. `hooks.py`, `hostel360/setup/` and `hostel360/utils/` are shared:
  change them only with the technical lead.
- **Tender obligations.** A change that touches a clause in
  [docs/COMPLIANCE-MATRIX.md](docs/COMPLIANCE-MATRIX.md) updates that row.

## Workflow

1. Branch from `main`: `feature/<short-name>`, `fix/<ticket>`, `docs/<topic>`.
2. Make the change with its tests. Install the hooks once: `pip install pre-commit &&
   pre-commit install` (ruff, Prettier for `frontend/`, JSON/YAML/TOML checks, whitespace).
   The portal is not yet uniformly Prettier-formatted, so the first commit touching a portal
   file may reformat it: review that part of the diff on its own.
3. Run the relevant checks locally (see the pull request template).
4. Open a pull request against `main`. The **CI** and **Security** workflows must be green,
   and one reviewer (the module owner, or the technical lead for shared files) approves.
5. Squash-merge with a message that says what changed and why.

### Commit messages

```
<area>: <what changed, imperative, at most 72 characters>

<why, and anything a reviewer or the operations team needs to know: migrations,
settings to change, messages that will now be sent>
```

Areas: `people`, `fees`, `mess`, `services`, `comms`, `ops`, `migration`, `portal`, `api`,
`security`, `deploy`, `docs`, `ci`.

## What a change must include

| Change | Also |
|---|---|
| DocType JSON | bump `modified`; `bench migrate` clean; permissions per the design matrix |
| New or changed business rule | tests for the happy path, each validation and the permissions |
| New endpoint | typed parameters, `methods=["POST"]` for writes, explicit permission or ownership check, rate limit for guests; [docs/API.md](docs/API.md) |
| Anything a user sees | translatable strings (`_()`, `__()`, `i18n/en.json` and `ta.json`); the user manual of that role |
| Message to students or parents | an English and a Tamil template; DLT and WhatsApp registration noted for go-live |
| Something operations must react to | an alert key with a clear title and the row in the [alert catalogue](docs/OPERATIONS-RUNBOOK.md#4-alert-catalogue) |
| Infrastructure | the checks in `deploy/README.md` §5 for the files touched |
| Anything released | an entry under *Unreleased* in [CHANGELOG.md](CHANGELOG.md) |

## Releases

1. Move the *Unreleased* entries under a new version heading in `CHANGELOG.md` and set
   `__version__` in `hostel360/__init__.py`.
2. Merge, then tag the merge commit `vX.Y.Z` and push the tag: the **Image** workflow builds
   and pushes `X.Y.Z-<sha>`.
3. Deploy to staging, check, then run **Deploy** for production in an agreed quiet hour
   (approval by the `production` environment's reviewers). See
   [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#3-continuous-integration-and-delivery).

## Security

Never commit secrets, real student or parent data, or production exports. Test fixtures use
made-up values. Report vulnerabilities as described in [SECURITY.md](SECURITY.md).
