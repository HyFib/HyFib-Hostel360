"""Summarise CodeQL SARIF results and fail the job on high or critical findings.

Usage: python3 .github/security/codeql_gate.py <sarif directory> <language>

GitHub's default CodeQL setup owns this repository's Security tab, so the workflow job keeps
its own results in the run: every finding is listed in the job summary and annotated on the
pull request, and the job fails when a finding is high or critical. The threshold is the
CVSS-based "security-severity" CodeQL assigns to its security queries (7.0 and above), which
is the same bar GitHub uses for failing code scanning checks by default.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

FAIL_AT = 7.0


def severity_label(score: float | None, level: str) -> str:
	if score is None:
		return level
	if score >= 9.0:
		return "critical"
	if score >= 7.0:
		return "high"
	if score >= 4.0:
		return "medium"
	return "low"


def rules_of(run: dict) -> dict:
	"""Rule id -> (default level, security severity) from the driver and its extensions."""
	rules = {}
	components = [run.get("tool", {}).get("driver", {}), *run.get("tool", {}).get("extensions", [])]
	for component in components:
		for rule in component.get("rules", []) or []:
			properties = rule.get("properties", {}) or {}
			score = properties.get("security-severity")
			rules[rule.get("id")] = (
				(rule.get("defaultConfiguration", {}) or {}).get("level", "warning"),
				float(score) if score not in (None, "") else None,
			)
	return rules


def findings(sarif_dir: Path):
	for path in sorted(sarif_dir.glob("*.sarif")):
		data = json.loads(path.read_text(encoding="utf-8"))
		for run in data.get("runs", []):
			rules = rules_of(run)
			for result in run.get("results", []) or []:
				if result.get("suppressions"):
					continue
				rule_id = result.get("ruleId") or (result.get("rule", {}) or {}).get("id", "?")
				default_level, score = rules.get(rule_id, ("warning", None))
				level = result.get("level") or default_level
				location = (result.get("locations") or [{}])[0].get("physicalLocation", {})
				yield {
					"rule": rule_id,
					"level": level,
					"score": score,
					"severity": severity_label(score, level),
					"file": location.get("artifactLocation", {}).get("uri", "?"),
					"line": location.get("region", {}).get("startLine", 1),
					"message": " ".join((result.get("message", {}) or {}).get("text", "").split()),
				}


def escape(value: str) -> str:
	return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main() -> int:
	sarif_dir, language = Path(sys.argv[1]), sys.argv[2]
	if not any(sarif_dir.glob("*.sarif")):
		print(f"::error::CodeQL produced no SARIF results in {sarif_dir}")
		return 1

	results = sorted(findings(sarif_dir), key=lambda f: (-(f["score"] or 0), f["file"], f["line"]))
	blocking = [f for f in results if (f["score"] or 0) >= FAIL_AT]

	lines = [f"## CodeQL ({language})", ""]
	if not results:
		lines.append("No findings.")
	else:
		lines += [
			f"{len(results)} finding(s), {len(blocking)} high or critical.",
			"",
			"| Severity | Rule | Location | Message |",
			"|---|---|---|---|",
		]
		for f in results:
			message = f["message"].replace("|", "\\|")
			lines.append(f"| {f['severity']} | `{f['rule']}` | `{f['file']}:{f['line']}` | {message} |")
	summary = os.environ.get("GITHUB_STEP_SUMMARY")
	if summary:
		with open(summary, "a", encoding="utf-8") as handle:
			handle.write("\n".join(lines) + "\n")
	print("\n".join(lines))

	for f in results:
		command = "error" if f in blocking else "warning"
		title = escape(f"CodeQL {f['severity']}: {f['rule']}")
		print(f"::{command} file={f['file']},line={f['line']},title={title}::{escape(f['message'])}")

	return 1 if blocking else 0


if __name__ == "__main__":
	sys.exit(main())
