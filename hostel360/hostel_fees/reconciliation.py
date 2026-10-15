"""Gateway reconciliation.

``reconcile_pending_transactions`` (every 15 minutes) settles online payments whose browser
callback and webhook never arrived: a phone that lost signal right after paying must still get
its receipt. It also looks again at transactions closed in the last few days, because a gateway
order stays payable after Hostel360 closes it (a retry in a checkout left open, a bank's late
authorisation). ``daily_reconciliation`` (23:30) checks that every successful payment has exactly
one matching receipt and verifies recent payments with the gateway, including refunds made
from the gateway dashboard and extra payments captured on an already paid order.

Each transaction is settled with the gateway that took it. Settling needs only that gateway's
credentials, not online payments being enabled: payments started before collection was
switched off are still receipted.
"""

import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, cint, get_datetime, now_datetime

from hostel360.hostel_fees import alerting
from hostel360.hostel_fees.constants import (
	CLOSED_TX_STATUSES,
	LIVE_TX_STATUSES,
	ONLINE,
	TRANSACTION,
	TX_SUCCESS,
)
from hostel360.hostel_fees.gateways import GatewayError, SettlementGateways
from hostel360.hostel_fees.gateways.base import CAPTURED, from_minor_units, to_minor_units
from hostel360.hostel_fees.transactions import (
	PaymentVerificationError,
	complete_transaction,
	mode_mismatch,
	recheck_closed_transaction,
	record_gateway_refund,
	settle_with_gateway,
)
from hostel360.hostel_fees.utils import get_payment_settings, money

# Give the checkout callback and the webhook a head start before asking the gateway.
SETTLE_AFTER_MINUTES = 10
BATCH_SIZE = 200
UNREACHABLE_ALERT_KEY = "payment-gateway-unreachable"
SAVEPOINT = "h360_reconcile_tx"
DAILY_ALERT_KEY = "payments-daily-reconciliation"
VERIFY_LOOKBACK_DAYS = 3
# Late authorisations and payments in checkouts left open arrive within days, not weeks.
CLOSED_LOOKBACK_DAYS = 7
# Recently closed transactions are looked at hourly, older ones a few times a day.
CLOSED_RECHECK_MINUTES = 60
CLOSED_RECHECK_MINUTES_AFTER_A_DAY = 6 * 60


def reconcile_pending_transactions() -> dict:
	settings = get_payment_settings()
	gateways = SettlementGateways(settings)
	if not gateways.any_configured():
		return {"skipped": True}

	expiry = cint(settings.transaction_expiry_minutes) or 60
	rows = frappe.get_all(
		TRANSACTION,
		filters={
			"status": ("in", LIVE_TX_STATUSES),
			"creation": ("<", add_to_date(now_datetime(), minutes=-SETTLE_AFTER_MINUTES)),
		},
		fields=["name", "gateway"],
		order_by="creation asc",
		limit=BATCH_SIZE,
	)

	run = _Run()
	for row in rows:
		gateway = gateways.get(row.gateway)
		if not gateway:
			run.count("skipped")
			continue
		run.attempt(row.name, settle_with_gateway, gateway, "reconciliation", expiry_minutes=expiry)

	recheck_closed_transactions(gateways, run)
	run.report_reachability()
	_commit()
	return run.summary()


def recheck_closed_transactions(gateways: SettlementGateways, run: "_Run | None" = None) -> "_Run":
	"""Ask the gateway about recently closed transactions that never got a receipt."""
	run = run or _Run()
	now = now_datetime()
	candidates = frappe.get_all(
		TRANSACTION,
		filters={
			"status": ("in", CLOSED_TX_STATUSES),
			"payment": ("is", "not set"),
			"gateway_order_id": ("is", "set"),
			"creation": (">=", add_days(now, -CLOSED_LOOKBACK_DAYS)),
		},
		fields=["name", "gateway", "creation", "last_checked_on", "test_mode"],
		order_by="last_checked_on asc",
		limit=BATCH_SIZE * 5,
	)
	due = [row for row in candidates if _closed_recheck_due(row, now)][:BATCH_SIZE]
	for row in due:
		gateway = gateways.get(row.gateway)
		if not gateway or mode_mismatch(row, gateway):
			continue
		run.attempt(row.name, recheck_closed_transaction, gateway, "reconciliation", closed=True)
	return run


def _closed_recheck_due(row, now) -> bool:
	if not row.last_checked_on:
		return True
	age = now - get_datetime(row.creation)
	interval = CLOSED_RECHECK_MINUTES if age.days < 1 else CLOSED_RECHECK_MINUTES_AFTER_A_DAY
	return get_datetime(row.last_checked_on) < add_to_date(now, minutes=-interval)


class _Run:
	"""Outcome of one reconciliation run; each transaction is committed on its own."""

	def __init__(self):
		self.outcome = {}
		self.checked = 0
		self.rechecked = 0
		self.unreachable = []
		self.unreadable = []
		self.errors = []
		self.refused = []

	def count(self, key: str) -> None:
		self.outcome[key] = self.outcome.get(key, 0) + 1

	def attempt(self, name: str, settle, *args, closed: bool = False, **kwargs) -> None:
		"""Run ``settle(name, *args, **kwargs)`` for one transaction and file the outcome."""
		if closed:
			self.rechecked += 1
		else:
			self.checked += 1
		frappe.db.savepoint(SAVEPOINT)
		try:
			status = settle(name, *args, **kwargs)
			if not closed:
				self.count(status)
			elif status == TX_SUCCESS:
				self.count("recovered")
			_commit()
		except PaymentVerificationError:
			# Already recorded and alerted; the transaction is now Failed.
			self.refused.append(name)
			_commit()
		except GatewayError as e:
			_undo()
			if e.is_unreachable:
				self.unreachable.append(f"{name}: {e}")
			else:
				# The gateway answered but cannot read this order. A real payment could sit behind
				# it, so it is never closed automatically: staff check the dashboard.
				self.unreadable.append(name)
				_record_unreadable(name, e, closed)
				_commit()
		except Exception:
			_undo()
			self.errors.append(name)
			frappe.log_error(
				title=f"Reconciliation of {name} failed", reference_doctype=TRANSACTION, reference_name=name
			)

	def report_reachability(self) -> None:
		asked = self.checked + self.rechecked
		if self.unreachable and len(self.unreachable) >= max(3, asked // 2):
			alerting.raise_alert(
				UNREACHABLE_ALERT_KEY,
				_("Payment gateway checks are failing"),
				severity="Warning",
				category="Payments",
				description=_(
					"{0} of {1} online payments could not be checked with the gateway. Students may "
					"not receive receipts until this is resolved."
				).format(len(self.unreachable), asked),
				details={"errors": self.unreachable[:20]},
			)
		elif asked and not self.unreachable:
			alerting.resolve_alert(UNREACHABLE_ALERT_KEY, notes=_("Gateway checks succeeded again."))

	def summary(self) -> dict:
		return {
			"checked": self.checked,
			"rechecked": self.rechecked,
			"errors": len(self.unreachable) + len(self.unreadable) + len(self.errors),
			"unreachable": len(self.unreachable),
			"unreadable": len(self.unreadable),
			**self.outcome,
		}


def _record_unreadable(name: str, error: GatewayError, closed: bool = False) -> None:
	if closed:
		# Keep the reason it was closed; just wait before asking again.
		frappe.db.set_value(TRANSACTION, name, "last_checked_on", now_datetime(), update_modified=False)
		return
	detail = error.detail or str(error)
	frappe.db.set_value(
		TRANSACTION,
		name,
		{
			"last_checked_on": now_datetime(),
			"failure_reason": _("The gateway could not read this order: {0}").format(detail)[:500],
		},
		update_modified=False,
	)
	alerting.raise_alert(
		f"payment-order-unreadable-{name}",
		_("Payment gateway cannot read transaction {0}").format(name),
		severity="Warning",
		category="Payments",
		description=_(
			"The gateway answered but could not find or read the order of transaction {0} ({1}). Check it "
			"in the gateway dashboard. If no money was taken, close it with 'Close as Expired'; if it "
			"was paid, record the receipt manually."
		).format(name, detail),
		details={"transaction": name, "http_status": error.http_status, "code": error.code},
	)


def daily_reconciliation() -> dict:
	issues = []
	issues += _successful_transactions_without_receipts()
	issues += _online_receipts_without_successful_transactions()

	gateways = SettlementGateways(get_payment_settings())
	gateway_issues, verified = _verify_with_gateway(gateways)
	issues += gateway_issues

	run = recheck_closed_transactions(gateways)
	issues += [
		{"transaction": name, "issue": "Payment captured on a closed transaction was refused"}
		for name in run.refused
	]
	issues += [
		{"transaction": name, "issue": "Gateway cannot read the order"} for name in run.unreadable
	]

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
	return {"issues": len(issues), "verified": verified, "recovered": run.outcome.get("recovered", 0)}


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


def _verify_with_gateway(gateways: SettlementGateways) -> tuple[list[dict], int]:
	"""Confirm recent successful payments are still captured at the gateway, in full, and alone.

	The whole order is read, not only the receipted payment: a refund made from the dashboard
	(gateways keep a partly refunded payment "captured") or a second payment captured on the same
	order is reported. A transaction stays unverified while anything is wrong with it.
	"""
	rows = frappe.get_all(
		TRANSACTION,
		filters={
			"status": TX_SUCCESS,
			"reconciled_on": ("is", "not set"),
			"gateway_payment_id": ("is", "set"),
			"creation": (">=", add_days(now_datetime(), -VERIFY_LOOKBACK_DAYS)),
		},
		fields=["name", "amount", "gateway", "gateway_payment_id", "gateway_order_id", "test_mode"],
		limit=BATCH_SIZE,
	)
	issues, verified = [], 0
	for row in rows:
		gateway = gateways.get(row.gateway)
		if not gateway or mode_mismatch(row, gateway):
			continue
		try:
			order = gateway.fetch_order_status(row.gateway_order_id)
			payments = [frappe._dict(p) for p in order.get("payments") or []]
			payment = next((p for p in payments if p.payment_id == row.gateway_payment_id), None)
			if payment is None:
				payment = gateway.fetch_payment(row.gateway_payment_id)
		except GatewayError as e:
			issues.append({"transaction": row.name, "issue": f"Could not verify with the gateway: {e}"})
			continue

		problems = []
		if payment.status != CAPTURED:
			problems.append(f"Gateway reports the payment as {payment.status}")
		elif payment.amount != to_minor_units(row.amount) or payment.order_id != row.gateway_order_id:
			problems.append("Gateway amount or order differs from the transaction")
		if payment.amount_refunded:
			record_gateway_refund(row.name, payment, "reconciliation")
			problems.append(f"Gateway reports {from_minor_units(payment.amount_refunded)} refunded")
		extra = [p for p in payments if p.status == CAPTURED and p.payment_id != row.gateway_payment_id]
		for other in extra:
			complete_transaction(row.name, other, "reconciliation")
		if extra:
			problems.append(
				"Other payments captured on the same order: {0}".format(", ".join(p.payment_id for p in extra))
			)

		if problems:
			issues += [{"transaction": row.name, "issue": problem} for problem in problems]
		else:
			frappe.db.set_value(TRANSACTION, row.name, "reconciled_on", now_datetime(), update_modified=False)
			verified += 1
		_commit()
	return issues, verified


def _undo():
	"""Discard what the failed attempt wrote; earlier transactions are already committed."""
	try:
		frappe.db.rollback(save_point=SAVEPOINT)
	except Exception:
		# A retried row lock has already rolled the whole transaction back.
		frappe.db.rollback()


def _commit():
	if not frappe.in_test:
		frappe.db.commit()  # nosemgrep
