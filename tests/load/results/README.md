# Load test results

`tests/load/run.sh` writes two files per run here: `<time>-<profile>-<host>.txt`, the k6
summary with the pass/fail of every threshold, and `.json`, every metric. Neither contains
student numbers, API keys or the sign-in code.

| Run | Profile | Environment | Result |
|---|---|---|---|
| 2026-10-15 | smoke (5 portal + 1 desk users; and 2 portal users reading their numbers with the API key) | Development bench on a laptop (Frappe development server, no WAF) | All thresholds passed: no failed requests, p95 under 35 ms. A functional check of the journeys only, not capacity evidence |

The acceptance run (`load`, 1,500 portal users on the staging stack, see `../README.md`) is
added here with the release it was run against.
