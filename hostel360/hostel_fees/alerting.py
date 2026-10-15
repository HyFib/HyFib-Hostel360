"""Bridge from fee events to HyFib operations alerts.

Payment problems (bad webhook signatures, money received without a receipt, an unreachable
gateway) must reach the HyFib operations desk. If the operations module cannot be imported the
signal is written to the Error Log instead of being lost.

A failed alert never fails the payment work that raised it, with one exception: a deadlock.
When the operations module cannot open its own connection it writes the alert in the caller's
transaction, and a deadlock there means InnoDB has already rolled the caller's work back.
Carrying on would commit only what the caller writes afterwards (a receipt without its invoice
update, say), so the deadlock is passed on for the caller's retry or rollback to deal with.
"""

import json

import frappe


def raise_alert(
	key: str,
	title: str,
	severity: str = "Warning",
	category: str = "Payments",
	description: str = "",
	details: dict | None = None,
) -> str | None:
	try:
		from hostel360.hostel_operations.alerts import raise_alert as ops_raise_alert
	except ImportError:
		frappe.log_error(
			title=f"Hostel360 alert: {title}",
			message=f"{description}\n\n{json.dumps(details or {}, default=str, indent=1)}",
		)
		return None

	try:
		return ops_raise_alert(
			key,
			title,
			severity,
			category,
			source="App",
			description=description,
			details=details,
		)
	except frappe.QueryDeadlockError:
		raise
	except Exception:
		frappe.log_error(title=f"Hostel360 could not raise alert {key}")
		return None


def resolve_alert(key: str, notes: str = "") -> bool:
	try:
		from hostel360.hostel_operations.alerts import resolve_alert as ops_resolve_alert
	except ImportError:
		return False

	try:
		return bool(ops_resolve_alert(key, notes=notes, source="App"))
	except frappe.QueryDeadlockError:
		raise
	except Exception:
		frappe.log_error(title=f"Hostel360 could not resolve alert {key}")
		return False
