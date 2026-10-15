"""Online payment transactions: turning a gateway-confirmed payment into a receipt exactly once.

Several paths can confirm the same payment - the browser checkout callback or return post, the
gateway webhooks and the reconciliation job - and they routinely arrive at the same moment.
Every path goes through ``complete_transaction``, which locks the transaction row, so whichever
arrives first issues the receipt and the others return that receipt.

Closing a transaction locally (Failed, Expired) does not stop the gateway from taking money on
that order later: a payer may retry in a checkout window that is still open, and banks can
authorise a payment hours after it was reported failed. ``recheck_closed_transaction`` looks at
recently closed transactions again so such payments are still receipted.
"""

import functools
import json
from datetime import UTC, datetime

import frappe
from frappe import _
from frappe.utils import (
	add_to_date,
	cint,
	convert_utc_to_system_timezone,
	get_datetime,
	getdate,
	now_datetime,
	nowdate,
)

from hostel360.hostel_fees import alerting
from hostel360.hostel_fees.constants import (
	CLOSED_TX_STATUSES,
	LIVE_TX_STATUSES,
	ONLINE,
	PAYMENT,
	TRANSACTION,
	TRANSACTION_INVOICE,
	TX_EXPIRED,
	TX_FAILED,
	TX_PENDING,
	TX_REFUNDED,
	TX_SUCCESS,
)
from hostel360.hostel_fees.gateways.base import (
	AUTHORIZED,
	CAPTURED,
	FAILED,
	GatewayError,
	PaymentGateway,
	from_minor_units,
	to_minor_units,
)
from hostel360.hostel_fees.utils import money, persist_now


class PaymentVerificationError(frappe.ValidationError):
	"""The gateway's answer does not match what Hostel360 asked for. Never issue a receipt."""


def retry_on_snapshot_conflict(fn):
	"""Run ``fn`` once more in a fresh database transaction if its row lock met a newer row.

	Under MariaDB's snapshot isolation a locking read of a row that another request changed after
	this request's first read fails with "record has changed since last read" (or a deadlock), and
	the server has already rolled the whole transaction back. Starting again with the lock as the
	first statement reads the committed row, so the second confirmation of a payment simply finds
	it complete. Callers must not have written anything they need before calling ``fn``.
	"""

	@functools.wraps(fn)
	def wrapper(*args, **kwargs):
		try:
			return fn(*args, **kwargs)
		except frappe.QueryDeadlockError:
			frappe.db.rollback()
			return fn(*args, **kwargs)

	return wrapper


def lock_transaction(name: str):
	"""Row-lock the transaction for the rest of the database transaction and return a fresh copy."""
	if not frappe.db.sql(
		"select name from `tabHostel Payment Transaction` where name = %(name)s for update",
		{"name": name},
	):
		frappe.throw(_("Payment transaction {0} was not found.").format(name), frappe.DoesNotExistError)
	return frappe.get_doc(TRANSACTION, name)


def lock_invoices(names) -> dict:
	"""Row-lock invoices in a stable order (avoids deadlocks between concurrent payments)."""
	names = sorted({name for name in names if name})
	if not names:
		return {}
	rows = frappe.db.sql(
		"""
		select name, docstatus, student, outstanding_amount, total_amount, status
		from `tabHostel Fee Invoice`
		where name in %(names)s
		order by name
		for update
		""",
		{"names": tuple(names)},
		as_dict=True,
	)
	return {row.name: row for row in rows}


def result(tx) -> frappe._dict:
	"""Response shape shared by checkout confirmation and the portal."""
	return frappe._dict(
		status=tx.status,
		payment=tx.payment if tx.status == TX_SUCCESS else None,
		receipt_number=tx.payment if tx.status == TX_SUCCESS else None,
		transaction=tx.name,
	)


@retry_on_snapshot_conflict
def complete_transaction(name: str, payment: dict, source: str) -> frappe._dict:
	"""Issue the receipt for a captured gateway payment. Safe to call repeatedly.

	``payment`` is a normalised gateway payment (see ``PaymentGateway.payment``).
	Raises PaymentVerificationError (after recording why and alerting HyFib) when the payment does
	not belong to this order or its amount differs. A second captured payment on an order that
	already has its receipt is never receipted again; it is recorded and HyFib is alerted so the
	extra money can be refunded.
	"""
	payment = frappe._dict(payment)
	tx = lock_transaction(name)

	if tx.status in (TX_SUCCESS, TX_REFUNDED):
		if _is_extra_capture(tx, payment):
			_record_extra_capture(tx, payment, source)
		elif tx.status == TX_REFUNDED:
			frappe.logger("hostel360").info(f"Ignoring {source} confirmation for refunded transaction {tx.name}")
		return result(tx)

	problem = _verification_problem(tx, payment)
	if problem:
		_reject(tx, payment, problem, source)

	receipt = _create_receipt(tx, payment)
	tx.db_set(
		{
			"status": TX_SUCCESS,
			"payment": receipt.name,
			"gateway_payment_id": payment.payment_id,
			"payment_method": payment.method,
			"completed_on": now_datetime(),
			"failure_reason": None,
			"verification_failed": 0,
			"last_checked_on": now_datetime(),
			"gateway_response": _as_json(payment.raw),
		},
		notify=True,
	)
	frappe.logger("hostel360").info(
		f"Payment transaction {tx.name} completed by {source}: receipt {receipt.name}"
	)
	return result(tx)


def _is_extra_capture(tx, payment) -> bool:
	return bool(
		payment.get("payment_id")
		and payment.get("status") == CAPTURED
		and payment.get("order_id") == tx.gateway_order_id
		and payment.payment_id != tx.gateway_payment_id
	)


def _record_extra_capture(tx, payment, source: str) -> None:
	"""Money captured twice on one order: keep the evidence and get it refunded."""
	amount = from_minor_units(payment.amount)
	already_noted = frappe.db.exists(
		"Comment",
		{
			"reference_doctype": TRANSACTION,
			"reference_name": tx.name,
			"comment_type": "Info",
			"content": ("like", f"%{payment.payment_id}%"),
		},
	)
	if not already_noted:
		tx.add_comment(
			"Info",
			_(
				"Another captured payment {0} ({1}, {2}) was reported by {3} for this order. "
				"It was not receipted."
			).format(
				frappe.utils.escape_html(payment.payment_id),
				amount,
				frappe.utils.escape_html(payment.method or _("unknown method")),
				source,
			),
		)
	alerting.raise_alert(
		f"payment-multiple-captures-{tx.name}",
		_("Order {0} has more than one captured payment").format(tx.gateway_order_id),
		severity="Warning",
		category="Payments",
		description=_(
			"Transaction {0} (student {1}) is already receipted as {2} with payment {3}. Payment {4} of {5} "
			"was also captured on the same order and has no receipt. Refund it from the gateway dashboard."
		).format(tx.name, tx.student, tx.payment, tx.gateway_payment_id, payment.payment_id, amount),
		details={
			"transaction": tx.name,
			"order_id": tx.gateway_order_id,
			"receipted_payment_id": tx.gateway_payment_id,
			"extra_payment_id": payment.payment_id,
			"amount": amount,
			"source": source,
		},
	)


def _verification_problem(tx, payment) -> str | None:
	if payment.status != CAPTURED:
		return _("payment status is {0}, expected captured").format(payment.status or "unknown")
	if not tx.gateway_order_id or payment.order_id != tx.gateway_order_id:
		return _("payment belongs to order {0}, expected {1}").format(payment.order_id, tx.gateway_order_id)
	expected = to_minor_units(tx.amount)
	if cint(payment.amount) != expected:
		return _("amount mismatch: expected {0} paise, gateway reports {1} paise").format(
			expected, cint(payment.amount)
		)
	if (payment.currency or "").upper() != (tx.currency or "INR").upper():
		return _("currency mismatch: expected {0}, gateway reports {1}").format(tx.currency, payment.currency)
	return None


def _reject(tx, payment, problem: str, source: str):
	"""Record why a confirmation was refused, alert HyFib, keep the evidence and raise."""
	tx.db_set(
		{
			"status": TX_FAILED if tx.status in LIVE_TX_STATUSES or tx.status == TX_EXPIRED else tx.status,
			"failure_reason": _("Rejected ({0}): {1}").format(source, problem)[:500],
			"gateway_payment_id": payment.payment_id or tx.gateway_payment_id,
			"verification_failed": 1,
			"last_checked_on": now_datetime(),
			"gateway_response": _as_json(payment.raw),
		}
	)
	alerting.raise_alert(
		f"payment-verification-{tx.name}",
		_("Online payment {0} failed verification").format(tx.name),
		severity="Warning",
		category="Payments",
		description=_(
			"A {0} confirmation for transaction {1} (student {2}) was refused: {3}. "
			"No receipt was issued. Check the payment in the gateway dashboard and refund or receipt it manually."
		).format(source, tx.name, tx.student, problem),
		details={
			"transaction": tx.name,
			"order_id": tx.gateway_order_id,
			"payment_id": payment.payment_id,
			"expected_amount": tx.amount,
			"gateway_amount": from_minor_units(payment.amount),
			"source": source,
		},
	)
	persist_now()
	raise PaymentVerificationError(
		_(
			"We could not verify this payment, so no receipt has been issued. If money was debited it "
			"will be refunded or confirmed by the hostel office. Please quote reference {0}."
		).format(tx.name)
	)


def _payment_date(payment) -> str:
	"""Receipt date = the day the payer paid (site time), not the day reconciliation noticed it."""
	created_at = cint(payment.get("created_at"))
	if created_at:
		try:
			paid_at = datetime.fromtimestamp(created_at, tz=UTC)
			return str(getdate(convert_utc_to_system_timezone(paid_at)))
		except OverflowError, OSError, ValueError:
			pass
	return nowdate()


def _create_receipt(tx, payment):
	"""Create and submit the Hostel Payment for a verified gateway payment."""
	amount = money(tx.amount)
	invoices = lock_invoices([row.invoice for row in tx.invoices])

	allocations, remaining = [], amount
	for row in tx.invoices:
		invoice = invoices.get(row.invoice)
		if not invoice or invoice.docstatus != 1 or invoice.student != tx.student:
			continue
		allocated = money(min(money(row.amount), money(invoice.outstanding_amount), remaining))
		if allocated <= 0:
			continue
		allocations.append({"invoice": row.invoice, "allocated_amount": allocated})
		remaining = money(remaining - allocated)

	receipt = frappe.new_doc(PAYMENT)
	receipt.update(
		{
			"student": tx.student,
			"hostel": tx.hostel,
			"payment_date": _payment_date(payment),
			"mode_of_payment": ONLINE,
			"amount": amount,
			"reference_no": payment.payment_id,
			"reference_date": _payment_date(payment),
			"payment_transaction": tx.name,
			# Test-mode money is not real: the receipt is marked and kept out of collections.
			"is_test": cint(tx.test_mode),
			"remarks": _("Paid online through {0}{1}.").format(
				tx.gateway or "the payment gateway", f" ({payment.method})" if payment.method else ""
			),
		}
	)
	for row in allocations:
		receipt.append("allocations", row)

	receipt.flags.ignore_permissions = True
	receipt.flags.from_gateway = True
	receipt.insert()
	receipt.submit()

	if remaining > 0:
		alerting.raise_alert(
			f"payment-unallocated-{tx.name}",
			_("Online payment {0} exceeds the dues it was meant for").format(tx.name),
			severity="Warning",
			category="Payments",
			description=_(
				"Receipt {0} for student {1} has {2} unallocated because the invoices were paid or "
				"cancelled while the payment was in progress. Adjust it against other dues or refund it."
			).format(receipt.name, tx.student, remaining),
			details={"transaction": tx.name, "receipt": receipt.name, "unallocated": remaining},
		)
	return receipt


@retry_on_snapshot_conflict
def mark_failed(name: str, reason: str, payment: dict | None = None, status: str = TX_FAILED) -> str:
	"""Close a live transaction as Failed or Expired; never downgrades a completed one."""
	tx = lock_transaction(name)
	if tx.status not in LIVE_TX_STATUSES:
		return tx.status
	payment = frappe._dict(payment or {})
	values = {
		"status": status,
		"failure_reason": (reason or "")[:500] or None,
		"last_checked_on": now_datetime(),
	}
	if payment.get("payment_id"):
		values["gateway_payment_id"] = payment.payment_id
		values["payment_method"] = payment.get("method")
	if payment.get("raw"):
		values["gateway_response"] = _as_json(payment.raw)
	tx.db_set(values, notify=True)
	return status


@retry_on_snapshot_conflict
def mark_pending(name: str, payment: dict | None = None, reason: str | None = None) -> str:
	"""A payment attempt exists but nothing is captured yet.

	``reason`` records why the last attempt failed when the payer can still retry on the same
	order; it is cleared when a newer attempt is under way. A live transaction with a failure
	reason is therefore waiting for the payer, not for the bank.
	"""
	tx = lock_transaction(name)
	if tx.status not in LIVE_TX_STATUSES:
		return tx.status
	payment = frappe._dict(payment or {})
	values = {
		"status": TX_PENDING,
		"last_checked_on": now_datetime(),
		"failure_reason": (reason or "")[:500] or None,
	}
	if payment.get("payment_id"):
		values["gateway_payment_id"] = payment.payment_id
		values["payment_method"] = payment.get("method")
	tx.db_set(values, notify=True)
	return TX_PENDING


@retry_on_snapshot_conflict
def record_gateway_refund(name: str, payment: dict, source: str) -> str:
	"""The gateway reports money refunded on this transaction's payment (possibly only part of it).

	Receipts and collection figures still show the full amount, so the refund is recorded on the
	transaction, verification is reopened and HyFib is alerted to adjust or cancel the receipt.
	"""
	payment = frappe._dict(payment)
	tx = lock_transaction(name)
	refunded = from_minor_units(payment.get("amount_refunded"))
	if refunded <= money(tx.get("refunded_amount")):
		return tx.status
	tx.db_set({"refunded_amount": refunded, "reconciled_on": None}, notify=True)
	tx.add_comment(
		"Info",
		_("The gateway reports {0} refunded on payment {1} ({2}).").format(
			refunded, frappe.utils.escape_html(payment.payment_id or tx.gateway_payment_id or ""), source
		),
	)
	alerting.raise_alert(
		f"payment-refunded-{tx.name}",
		_("Online payment {0} was refunded at the gateway").format(tx.name),
		severity="Warning",
		category="Payments",
		description=_(
			"The gateway reports {0} of {1} refunded for transaction {2} (student {3}). Receipt {4} still "
			"shows the full amount: cancel or adjust it so collections match the money kept."
		).format(refunded, money(tx.amount), tx.name, tx.student, tx.payment or "-"),
		details={
			"transaction": tx.name,
			"payment_id": payment.payment_id or tx.gateway_payment_id,
			"refunded": refunded,
			"amount": money(tx.amount),
			"receipt": tx.payment,
			"source": source,
		},
	)
	return tx.status


def capture_if_authorised(gateway: PaymentGateway, tx, payment, order: dict | None = None) -> frappe._dict:
	"""Capture an authorised payment so the institution actually receives the money.

	Gateways refund authorised-but-uncaptured payments after a few days, so issuing a receipt
	for one would be wrong. Hostel360 never captures a payment on a transaction that is already
	closed or paid, nor a second payment on an order that already has a captured one: those are
	left to lapse (the gateway refunds them) and HyFib is told. If the account captures
	automatically we may race it; a refusal is then followed by a fresh read.
	"""
	payment = frappe._dict(payment)
	if payment.status != AUTHORIZED or payment.order_id != tx.gateway_order_id:
		return payment

	status = frappe.db.get_value(TRANSACTION, tx.name, "status")
	if status not in LIVE_TX_STATUSES:
		_alert_uncaptured(tx, payment, _("the transaction is already {0}").format(_(status)))
		return payment

	order = order or gateway.fetch_order_status(tx.gateway_order_id)
	others = [
		p.get("payment_id")
		for p in order.get("payments") or []
		if p.get("status") == CAPTURED and p.get("payment_id") != payment.payment_id
	]
	if others:
		_alert_uncaptured(
			tx, payment, _("payment {0} on the same order is already captured").format(", ".join(others))
		)
		return payment

	try:
		return gateway.capture_payment(
			payment.payment_id, payment.amount or to_minor_units(tx.amount), tx.currency
		)
	except GatewayError:
		return gateway.fetch_payment(payment.payment_id)


def _alert_uncaptured(tx, payment, why: str) -> None:
	alerting.raise_alert(
		f"payment-authorised-not-captured-{tx.name}",
		_("Authorised payment on {0} was not captured").format(tx.name),
		severity="Warning",
		category="Payments",
		description=_(
			"Payment {0} of {1} on order {2} (student {3}) is authorised but was not captured because {4}. "
			"The gateway refunds it automatically unless it is captured from the dashboard."
		).format(payment.payment_id, from_minor_units(payment.amount), tx.gateway_order_id, tx.student, why),
		details={
			"transaction": tx.name,
			"order_id": tx.gateway_order_id,
			"payment_id": payment.payment_id,
			"amount": from_minor_units(payment.amount),
		},
	)


def mode_mismatch(tx, gateway: PaymentGateway) -> bool:
	"""The transaction was made with test credentials and the gateway now uses live ones, or the reverse.

	Gateways keep test and live data apart, so such an order cannot be read with today's keys.
	"""
	return bool(cint(tx.test_mode)) != gateway.test_mode


def settle_with_gateway(
	name: str,
	gateway: PaymentGateway,
	source: str,
	expiry_minutes: int | None = None,
	order: dict | None = None,
) -> str:
	"""Ask the gateway what happened to a live transaction and act on it.

	Returns the resulting transaction status. Used by reconciliation, by staff ("Check with
	gateway") and before superseding an abandoned checkout. ``order`` may carry an order status
	the caller has already fetched.
	"""
	tx = frappe.get_doc(TRANSACTION, name)
	if tx.status not in LIVE_TX_STATUSES:
		return tx.status
	if not tx.gateway_order_id:
		return mark_failed(name, _("No gateway order was created."), status=TX_EXPIRED)

	if order is None and mode_mismatch(tx, gateway):
		if cint(tx.test_mode):
			# A test-mode order never carries real money, and live keys cannot read it.
			return mark_failed(
				name, _("Test-mode payment closed because the gateway now uses live keys."), status=TX_EXPIRED
			)
		# A live order cannot be read with test keys; leave it until live keys are back.
		return tx.status

	order = order or gateway.fetch_order_status(tx.gateway_order_id)
	payments = [frappe._dict(p) for p in order.get("payments") or []]

	captured = [p for p in payments if p.status == CAPTURED]
	if captured:
		outcome = complete_transaction(name, captured[0], source).status
		for extra in captured[1:]:
			# Already receipted now, so each extra capture is recorded and alerted, never receipted.
			complete_transaction(name, extra, source)
		return outcome

	for candidate in (p for p in payments if p.status == AUTHORIZED):
		settled = capture_if_authorised(gateway, tx, candidate, order)
		if settled.status == CAPTURED:
			return complete_transaction(name, settled, source).status

	failed = [p for p in payments if p.status == FAILED]
	if failed and not gateway.reusable_orders and len(failed) == len(payments):
		# The gateway closed this order with the failed attempt; the payer must start a new payment.
		return mark_failed(name, failed[0].error_description or _("The payment did not go through."), failed[0])

	if expiry_minutes is None:
		from hostel360.hostel_fees.utils import get_payment_settings

		expiry_minutes = cint(get_payment_settings().transaction_expiry_minutes) or 60
	expired = get_datetime(tx.creation) < add_to_date(now_datetime(), minutes=-expiry_minutes)

	if expired:
		if failed:
			last = failed[0]
			reason = last.error_description or _("The payment did not go through.")
			return mark_failed(name, reason, last)
		return mark_failed(
			name,
			_("No payment was completed within {0} minutes.").format(expiry_minutes),
			status=TX_EXPIRED,
		)

	if payments:
		reason = None
		if failed and len(failed) == len(payments):
			reason = failed[0].error_description or _("The payment did not go through.")
		return mark_pending(name, payments[0], reason=reason)

	frappe.db.set_value(TRANSACTION, name, "last_checked_on", now_datetime(), update_modified=False)
	return tx.status


def recheck_closed_transaction(name: str, gateway: PaymentGateway, source: str) -> str:
	"""Look again at a Failed or Expired transaction whose order may still have been paid.

	A captured payment is receipted (the payer did pay; if the dues were settled some other way
	meanwhile, the receipt holds the money unallocated and HyFib is alerted). An authorised one is
	not captured: it lapses and the gateway refunds it, and HyFib is told.
	"""
	tx = frappe.get_doc(TRANSACTION, name)
	if tx.status not in CLOSED_TX_STATUSES or tx.payment or not tx.gateway_order_id:
		return tx.status
	if mode_mismatch(tx, gateway):
		return tx.status

	order = gateway.fetch_order_status(tx.gateway_order_id)
	payments = [frappe._dict(p) for p in order.get("payments") or []]
	captured = [p for p in payments if p.status == CAPTURED]
	if cint(tx.get("verification_failed")):
		# Captures already refused (and alerted) are not tried again every run.
		captured = [p for p in captured if p.payment_id != tx.gateway_payment_id]

	if captured:
		outcome = complete_transaction(name, captured[0], source).status
		for extra in captured[1:]:
			complete_transaction(name, extra, source)
		frappe.logger("hostel360").info(
			f"Closed payment transaction {name} had a captured payment; completed by {source}"
		)
		return outcome

	for candidate in (p for p in payments if p.status == AUTHORIZED):
		_alert_uncaptured(tx, candidate, _("the transaction is already {0}").format(_(tx.status)))

	frappe.db.set_value(TRANSACTION, name, "last_checked_on", now_datetime(), update_modified=False)
	return tx.status


def sync_with_gateway(
	name: str, gateway: PaymentGateway, source: str, expiry_minutes: int | None = None
) -> str:
	"""Bring a transaction in line with what the gateway says, whatever its local status."""
	status = frappe.db.get_value(TRANSACTION, name, "status")
	if status in LIVE_TX_STATUSES:
		return settle_with_gateway(name, gateway, source, expiry_minutes=expiry_minutes)
	if status in CLOSED_TX_STATUSES:
		return recheck_closed_transaction(name, gateway, source)
	return status


def get_live_transactions(invoices: list[str], expiry_minutes: int | None = None) -> dict:
	"""Live (Initiated/Pending, not yet expired) transactions covering any of ``invoices``.

	Returns ``{transaction: frappe._dict(name, student, amount, status, gateway, ...,
	invoices: {invoice: amount})}``.
	"""
	invoices = [name for name in invoices if name]
	if not invoices:
		return {}
	if expiry_minutes is None:
		from hostel360.hostel_fees.utils import get_payment_settings

		expiry_minutes = cint(get_payment_settings().transaction_expiry_minutes) or 60
	cutoff = add_to_date(now_datetime(), minutes=-expiry_minutes)

	tx = frappe.qb.DocType(TRANSACTION)
	row = frappe.qb.DocType(TRANSACTION_INVOICE)
	names = (
		frappe.qb.from_(row)
		.join(tx)
		.on(row.parent == tx.name)
		.select(tx.name)
		.distinct()
		.where(row.parenttype == TRANSACTION)
		.where(row.invoice.isin(invoices))
		.where(tx.status.isin(LIVE_TX_STATUSES))
		.where(tx.creation >= cutoff)
		.run(pluck=True)
	)
	if not names:
		return {}

	live = {}
	for record in frappe.get_all(
		TRANSACTION,
		filters={"name": ("in", names)},
		fields=[
			"name",
			"student",
			"amount",
			"creation",
			"status",
			"gateway",
			"gateway_order_id",
			"failure_reason",
		],
	):
		record.invoices = {}
		live[record.name] = record
	for item in frappe.get_all(
		TRANSACTION_INVOICE,
		filters={"parent": ("in", names), "parenttype": TRANSACTION},
		fields=["parent", "invoice", "amount"],
	):
		live[item.parent].invoices[item.invoice] = money(item.amount)
	return live


def _as_json(value) -> str | None:
	if not value:
		return None
	return json.dumps(value, indent=1, sort_keys=True, default=str)
