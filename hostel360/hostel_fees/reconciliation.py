"""Gateway reconciliation.

``reconcile_pending_transactions`` (every 15 minutes) settles online payments whose browser
callback and webhook never arrived: a phone that lost signal right after paying must still get
its receipt. ``daily_reconciliation`` (23:30) checks that every successful payment has exactly
one matching receipt and verifies the day's payments with the gateway.

Neither job calls the gateway while online payments are disabled or the keys are missing.
"""

import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, cint, now_datetime

from hostel360.hostel_fees import alerting
from hostel360.hostel_fees.constants import LIVE_TX_STATUSES, ONLINE, TRANSACTION, TX_SUCCESS
from hostel360.hostel_fees.gateways import GatewayError, get_gateway, is_gateway_configured
from hostel360.hostel_fees.gateways.base import CAPTURED, REFUNDED, to_minor_units
from hostel360.hostel_fees.transactions import PaymentVerificationError, settle_with_gateway
from hostel360.hostel_fees.utils import get_payment_settings, money

# Give the checkout callback and the webhook a head start before asking the gateway.
SETTLE_AFTER_MINUTES = 10
BATCH_SIZE = 200
UNREACHABLE_ALERT_KEY = "payment-gateway-unreachable"
DAILY_ALERT_KEY = "payments-daily-reconciliation"
VERIFY_LOOKBACK_DAYS = 3


def reconcile_pending_transactions() -> dict:
	settings = get_payment_settings()
	if not is_gateway_configured(settings):
		return {"skipped": True}

	gateway = get_gateway(settings)
	expiry = cint(settings.transaction_expiry_minutes) or 60
	names = frappe.get_all(
		TRANSACTION,
		filters={
			"status": ("in", LIVE_TX_STATUSES),
			"creation": ("<", add_to_date(now_datetime(), minutes=-SETTLE_AFTER_MINUTES)),
		},
		pluck="name",
		order_by="creation asc",
		limit=BATCH_SIZE,
	)

	outcome, errors = {}, []
	for name in names:
		try:
			status = settle_with_gateway(name, gateway, "reconciliation", expiry_minutes=expiry)
			outcome[status] = outcome.get(status, 0) + 1
			_commit()
		except PaymentVerificationError:
			# Already recorded and alerted; the transaction is now Failed and leaves the queue.
			_commit()
		except GatewayError as e:
			frappe.db.rollback()
			errors.append(f"{name}: {e}")
		except Exception:
			frappe.db.rollback()
			errors.append(name)
			frappe.log_error(
				title=f"Reconciliation of {name} failed", reference_doctype=TRANSACTION, reference_name=name
			)

	if errors and len(errors) >= max(3, len(names) // 2):
		alerting.raise_alert(
			UNREACHABLE_ALERT_KEY,
			_("Payment gateway checks are failing"),
			severity="Warning",
			category="Payments",
			description=_(
				"{0} of {1} pending online payments could not be checked with the gateway. Students may "
				"not receive receipts until this is resolved."
			).format(len(errors), len(names)),
			details={"errors": errors[:20]},
		)
	elif names and not errors:
		alerting.resolve_alert(UNREACHABLE_ALERT_KEY, notes=_("Gateway checks succeeded again."))

	_commit()
	return {"checked": len(names), "errors": len(errors), **outcome}


def daily_reconciliation() -> dict:
	issues = []
	issues += _successful_transactions_without_receipts()
	issues += _online_receipts_without_successful_transactions()

	verified = 0
	settings = get_payment_settings()
	if is_gateway_configured(settings):
		gateway_issues, verified = _verify_with_gateway(get_gateway(settings))
		issues += gateway_issues

	if issues:
		alerting.raise_alert(
			DAILY_ALERT_KEY,
			_("{0} online payment reconciliation issue(s)").format(len(issues)),
			severity="Warning",
			category="Payments",
			description=_(
				"The daily reconciliation found online payments whose receipts or gateway records do not "
				"match. Open the Gateway Reconciliation report for details."
			),
			details={"issues": issues[:50]},
		)
	else:
		alerting.resolve_alert(DAILY_ALERT_KEY, notes=_("Daily reconciliation found no issues."))

	_commit()
	return {"issues": len(issues), "verified": verified}


def _successful_transactions_without_receipts() -> list[dict]:
	rows = frappe.db.sql(
		"""
		select tx.name, tx.amount, tx.payment, pay.docstatus as receipt_docstatus, pay.amount as receipt_amount
		from `tabHostel Payment Transaction` tx
		left join `tabHostel Payment` pay on pay.name = tx.payment
		where tx.status = %(success)s
		""",
		{"success": TX_SUCCESS},
		as_dict=True,
	)
	issues = []
	for row in rows:
		if not row.payment or row.receipt_docstatus != 1:
			issues.append(
				{
					"transaction": row.name,
					"issue": "Successful payment without a submitted receipt",
					"receipt": row.payment,
				}
			)
		elif money(row.receipt_amount) != money(row.amount):
			issues.append(
				{
					"transaction": row.name,
					"issue": "Receipt amount differs from the payment",
					"receipt": row.payment,
					"amount": money(row.amount),
					"receipt_amount": money(row.receipt_amount),
				}
			)
	return issues


def _online_receipts_without_successful_transactions() -> list[dict]:
	rows = frappe.db.sql(
		"""
		select pay.name, pay.payment_transaction, tx.status
		from `tabHostel Payment` pay
		left join `tabHostel Payment Transaction` tx on tx.name = pay.payment_transaction
		where pay.docstatus = 1
			and pay.mode_of_payment = %(online)s
			and pay.payment_transaction is not null
			and pay.payment_transaction != ''
			and (tx.name is null or tx.status != %(success)s)
		""",
		{"online": ONLINE, "success": TX_SUCCESS},
		as_dict=True,
	)
	return [
		{
			"receipt": row.name,
			"transaction": row.payment_transaction,
			"issue": f"Online receipt whose transaction is {row.status or 'missing'}",
		}
		for row in rows
	]


def _verify_with_gateway(gateway) -> tuple[list[dict], int]:
	"""Confirm recent successful payments are still captured at the gateway (not refunded or reversed)."""
	rows = frappe.get_all(
		TRANSACTION,
		filters={
			"status": TX_SUCCESS,
			"reconciled_on": ("is", "not set"),
			"gateway_payment_id": ("is", "set"),
			"creation": (">=", add_days(now_datetime(), -VERIFY_LOOKBACK_DAYS)),
		},
		fields=["name", "amount", "gateway_payment_id", "gateway_order_id"],
		limit=BATCH_SIZE,
	)
	issues, verified = [], 0
	for row in rows:
		try:
			payment = gateway.fetch_payment(row.gateway_payment_id)
		except GatewayError as e:
			issues.append({"transaction": row.name, "issue": f"Could not verify with the gateway: {e}"})
			continue
		if payment.status == REFUNDED or payment.status != CAPTURED:
			issues.append(
				{"transaction": row.name, "issue": f"Gateway reports the payment as {payment.status}"}
			)
		elif payment.amount != to_minor_units(row.amount) or payment.order_id != row.gateway_order_id:
			issues.append(
				{"transaction": row.name, "issue": "Gateway amount or order differs from the transaction"}
			)
		else:
			frappe.db.set_value(TRANSACTION, row.name, "reconciled_on", now_datetime(), update_modified=False)
			verified += 1
	return issues, verified


def _commit():
	if not frappe.in_test:
		frappe.db.commit()  # nosemgrep
