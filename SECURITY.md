# Security policy

HyFib Hostel360 holds students' and parents' personal data and handles hostel fee payments
for ACGCET Hostels. We take reports of security weaknesses seriously and act on them quickly.

## Reporting a vulnerability

E-mail **security@hyfib.com** with:

- what you found and where (URL, endpoint, screen, file);
- the steps to reproduce it, and what an attacker could do with it;
- any proof-of-concept, logs or screenshots;
- how we can reach you.

Please do **not** open a public issue, post details anywhere, or test against the
production portal with other people's accounts or data. Use only your own account, stop as
soon as you have shown the problem, do not read, change or keep anyone else's data, and do
not run denial-of-service, spam or social-engineering tests.

If the matter is urgent (an attack in progress, data exposed right now), also phone the
HyFib helpdesk, **+91 96060 60596** (24×7), and say it is a security report.

## What happens next

| When | What |
|---|---|
| Within 1 working day | We acknowledge your report and give you a reference |
| Within 5 working days | We tell you whether we can reproduce it and how serious we consider it |
| As soon as fixed | We deploy the fix to every installation and tell you; we agree with you when details may be published |

If a report shows that a cyber security incident has happened, HyFib follows its incident
procedure: the institution and CERT-In are informed within six hours of detection, as the
CERT-In directions of 28 April 2022 require
([security incident runbook](deploy/monitoring/security-incident-runbook.md)).

We do not take legal action against anyone who reports in good faith and follows this
policy.

## Supported versions

Only the release running in production and the `main` branch receive security fixes. Fixes
are released as a new version (see [CHANGELOG.md](CHANGELOG.md)) and deployed with
`h360-deploy`.

## Scope

In scope: the Hostel360 application (`hostel360/`), the student and parent portal
(`frontend/`), the APIs under `/api/method/hostel360.*`, and the deployment configuration in
`deploy/`. Weaknesses in the Frappe Framework itself, in payment gateways or in messaging
providers should also be reported to their maintainers; tell us as well, so we can protect
our installations in the meantime.

How Hostel360 is secured is described in [docs/SECURITY.md](docs/SECURITY.md).
