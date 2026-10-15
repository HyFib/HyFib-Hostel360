"""Fee services (design section 5).

These functions hold the fee rules used by the portal API, the mess billing run, the data
migration and the desk helpers. They run with elevated document permissions, so every function
that acts for a person first checks that the person may act for the student.
"""

from contextlib import contextmanager

import frappe
from frappe import _
from frappe.model.naming import make_autoname
from frappe.translate import print_language
from frappe.utils import add_to_date, cint, get_datetime, get_url, getdate, now_datetime, nowdate

from hostel360.hostel_fees import transactions
from hostel360.hostel_fees.constants import (
	CURRENCY,
	INVOICE,
	INVOICE_ITEM,
	INVOICE_TYPES,
	MAX_INVOICES_PER_PAYMENT,
	PAYMENT,
	STRUCTURE,
	TRANSACTION,
	TX_EXPIRED,
	TX_FAILED,
	TX_REFUNDED,
	TX_SUCCESS,
)
from hostel360.hostel_fees.gateways import (
	GatewayError,
	GatewayNotConfiguredError,
	get_gateway,
	get_gateway_code,
	get_settlement_gateway,
	is_gateway_configured,
)
from hostel360.hostel_fees.gateways.base import AUTHORIZED, CAPTURED, CHECKOUT_MODAL, FAILED, ORDER_CREATED
from hostel360.hostel_fees.utils import (
	get_current_academic_year,
	get_hostel_setting,
	get_institution_short_name,
	get_payment_settings,
	get_student_hostel,
	money,
	persist_now,
)
from hostel360.setup.roles import ADMIN_ROLES
from hostel360.utils.context import (
	assert_can_access_student,
	get_guardian_for_user,
	get_student_for_user,
	has_any_role,
)

# A reused checkout closes this long before the transaction expires, so it cannot be paid after
# Hostel360 has released the dues for a new payment.
CHECKOUT_TIMEOUT_MARGIN_SECONDS = 60
MIN_CHECKOUT_TIMEOUT_SECONDS = 60

# ------------------------------------------------------------------------------- summaries


def get_fee_summary(student: str) -> dict:
	"""Dues and payment history of a student: {outstanding, overdue, next_due_date, invoices, payments}."""
	assert_can_access_student(student)
	today = getdate(nowdate())

	invoices = frappe.get_all(
		INVOICE,
		filters={"student": student, "docstatus": 1},
		fields=[
			"name",
			"invoice_type",
			"academic_year",
			"posting_date",
			"due_date",
			"total_amount",
			"paid_amount",
			"outstanding_amount",
			"status",
		],
		order_by="posting_date desc, creation desc",
		limit=500,
	)
	names = [row.name for row in invoices]

	items_by_invoice = {}
	if names:
		for item in frappe.get_all(
			INVOICE_ITEM,
			filters={"parent": ("in", names), "parenttype": INVOICE},
			fields=["parent", "fee_head", "description", "amount"],
			order_by="idx asc",
		):
			items_by_invoice.setdefault(item.parent, []).append(
				{"fee_head": item.fee_head, "description": item.description, "amount": money(item.amount)}
			)

	open_names = [row.name for row in invoices if money(row.outstanding_amount) > 0]
	in_progress = set()
	for tx in transactions.get_live_transactions(open_names).values():
		if tx.failure_reason:
			# The last attempt failed and the payer may try again: paying reuses or safely
			# replaces that order (see create_payment_order), so the dues stay payable.
			continue
		in_progress.update(tx.invoices)

	outstanding = overdue = 0.0
	overdue_count = 0
	next_due_date = None
	for row in invoices:
		row.items = items_by_invoice.get(row.name, [])
		row.payment_in_progress = row.name in in_progress
		balance = money(row.outstanding_amount)
		if balance <= 0:
			continue
		outstanding += balance
		due = getdate(row.due_date) if row.due_date else None
		if due and due < today:
			overdue += balance
			overdue_count += 1
		elif due and (next_due_date is None or due < next_due_date):
			next_due_date = due

	payments = frappe.get_all(
		PAYMENT,
		filters={"student": student, "docstatus": 1},
		fields=["name", "payment_date", "amount", "mode_of_payment", "reference_no"],
		order_by="payment_date desc, creation desc",
		limit=500,
	)

	return {
		"student": student,
		"outstanding": money(outstanding),
		"overdue": money(overdue),
		"overdue_count": overdue_count,
		"next_due_date": next_due_date,
		"invoices": invoices,
		"payments": payments,
		"payment_enabled": online_payment_available(student),
	}


def online_payment_available(student: str, user: str | None = None, settings=None) -> bool:
	"""Whether the portal should offer online payment for this student right now."""
	settings = settings or get_payment_settings()
	if not is_gateway_configured(settings):
		return False
	return not cint(settings.test_mode) or _may_pay_in_test_mode(settings, student, user)


def _may_pay_in_test_mode(settings, student: str, user: str | None = None) -> bool:
	"""While test credentials are in use only designated test students (or administrators trying
	the flow from the desk) may pay, so no real dues are cleared by test money."""
	if student in {row.student for row in settings.get("test_payers") or []}:
		return True
	return has_any_role(ADMIN_ROLES, user)


# -------------------------------------------------------------------------------- invoices


def create_invoice(
	student,
	items,
	invoice_type,
	due_date,
	reference_doctype=None,
	reference_name=None,
	posting_date=None,
	remarks=None,
	submit=True,
	academic_year=None,
	fee_structure=None,
	hostel=None,
) -> str:
	"""Create (and by default submit) an invoice for a student.

	``items`` is a list of ``{fee_head, amount, description?}``. Used by fee structures, the
	monthly mess bill run, fines and the legacy data migration.

	The invoice is filed under the student's current (or last) hostel. ``hostel`` files it under
	another one instead; only server code that knows better passes it (the mess bill run bills
	the hostel whose mess the student ate in).
	"""
	if invoice_type not in INVOICE_TYPES:
		frappe.throw(_("Invoice type must be one of {0}.").format(", ".join(INVOICE_TYPES)))
	if not items:
		frappe.throw(_("An invoice needs at least one fee item."))

	doc = frappe.new_doc(INVOICE)
	doc.update(
		{
			"student": student,
			"invoice_type": invoice_type,
			"due_date": due_date,
			"posting_date": posting_date or nowdate(),
			"academic_year": academic_year or get_current_academic_year(),
			"fee_structure": fee_structure,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"remarks": remarks,
		}
	)
	if hostel:
		doc.hostel = hostel
		doc.flags.keep_hostel = True

	for item in items:
		item = frappe._dict(item)
		doc.append(
			"items",
			{
				"fee_head": item.fee_head,
				"description": item.get("description"),
				"amount": money(item.amount),
			},
		)

	doc.flags.ignore_permissions = True
	doc.insert()
	if submit:
		doc.submit()
	return doc.name


def cancel_invoice(invoice: str) -> None:
	"""Withdraw an invoice. Drafts are deleted; submitted invoices are cancelled if nothing was paid."""
	doc = frappe.get_doc(INVOICE, invoice)
	if doc.docstatus == 2:
		return
	if doc.docstatus == 0:
		frappe.delete_doc(INVOICE, doc.name, ignore_permissions=True)
		return
	doc.flags.ignore_permissions = True
	doc.cancel()


# ------------------------------------------------------------------------ online payment


def create_payment_order(student: str, invoices: list[str], user: str, ip_address: str | None = None) -> dict:
	"""Start an online payment for some of a student's invoices.

	Returns ``{transaction, gateway, amount, test_mode, checkout}`` where ``checkout`` holds what
	the portal needs to open the gateway's checkout (see ``PaymentGateway.checkout_options``).
	Asking again for exactly the same invoices while a payment is still open returns the same
	order where the gateway allows it, so a double tap or a page refresh never creates a second
	charge.
	"""
	assert_can_access_student(student, user)
	invoices = _clean_invoice_list(invoices)
	settings = get_payment_settings()
	gateway = get_gateway(settings)
	if cint(settings.test_mode) and not _may_pay_in_test_mode(settings, student, user):
		frappe.throw(
			_("Online payment is being tested and is not open yet. Please pay at the hostel office."),
			title=_("Online payment unavailable"),
		)

	requested = _payable_invoices(student, invoices)
	expiry = cint(settings.transaction_expiry_minutes) or 60

	# Resolve earlier attempts before taking invoice locks: transactions are always locked
	# before invoices, the same order the completion path uses.
	live = transactions.get_live_transactions(invoices, expiry)
	same = next(
		(
			tx
			for tx in live.values()
			if tx.student == student and tx.invoices == requested and tx.gateway == gateway.name
		),
		None,
	)
	if same:
		reusable = _reusable_order(same, gateway)
		if reusable is True:
			return checkout_payload(frappe.get_doc(TRANSACTION, same.name), user)
		_resolve_overlapping_transaction(same, gateway, expiry, reusable)
	for tx in live.values():
		if not same or tx.name != same.name:
			_resolve_overlapping_transaction(tx, _settlement_gateway_or_busy(tx, expiry), expiry)

	transactions.lock_invoices(invoices)
	requested = _payable_invoices(student, invoices)
	if transactions.get_live_transactions(invoices, expiry):
		frappe.throw(
			_("A payment for these dues is already in progress. Please wait a minute and try again."),
			title=_("Payment in progress"),
		)

	amount = money(sum(requested.values()))
	if amount < 1:
		frappe.throw(_("The amount to pay must be at least ₹1."))

	name = make_autoname("PTX-.YYYY.-.######", TRANSACTION)
	order = gateway.create_order(
		amount,
		CURRENCY,
		receipt=name,
		notes={
			"transaction": name,
			"student": student,
			"invoices": ", ".join(invoices),
			"institution": get_institution_short_name(),
		},
	)

	tx = frappe.new_doc(TRANSACTION)
	tx.update(
		{
			"student": student,
			"hostel": get_student_hostel(student),
			"amount": amount,
			"currency": CURRENCY,
			"gateway": gateway.name,
			"gateway_order_id": order["order_id"],
			"status": "Initiated",
			"initiated_by": user,
			"ip_address": ip_address,
			"test_mode": cint(settings.test_mode),
		}
	)
	for invoice in invoices:
		tx.append("invoices", {"invoice": invoice, "amount": requested[invoice]})
	tx.flags.ignore_permissions = True
	tx.insert(set_name=name)
	return checkout_payload(tx, user)


def _reusable_order(tx, gateway):
	"""True when the open order can be handed to the payer again; otherwise the order status.

	A gateway that takes retries on the same order (Razorpay) always can. Otherwise the order
	is reused only if the gateway has never seen an attempt on it.
	"""
	if gateway.reusable_orders:
		return True
	try:
		order = gateway.fetch_order_status(tx.gateway_order_id)
	except GatewayError:
		_throw_in_progress(cint(get_payment_settings().transaction_expiry_minutes) or 60)
	return True if order.get("status") == ORDER_CREATED else order


def _settlement_gateway_or_busy(tx, expiry: int):
	try:
		return get_settlement_gateway(tx.gateway)
	except GatewayNotConfiguredError:
		# The gateway that took this payment cannot be asked; wait for the transaction to expire.
		_throw_in_progress(expiry)


def _throw_in_progress(expiry: int):
	frappe.throw(
		_(
			"A payment for some of these dues is already in progress. Please complete it, or try again "
			"after {0} minutes."
		).format(expiry),
		title=_("Payment in progress"),
	)


def _clean_invoice_list(invoices) -> list[str]:
	if isinstance(invoices, str):
		parsed = frappe.parse_json(invoices) if invoices.strip().startswith("[") else [invoices]
		invoices = parsed
	names = []
	for name in invoices or []:
		name = str(name or "").strip()
		if name and name not in names:
			names.append(name)
	if not names:
		frappe.throw(_("Select at least one invoice to pay."))
	if len(names) > MAX_INVOICES_PER_PAYMENT:
		frappe.throw(_("You can pay at most {0} invoices at a time.").format(MAX_INVOICES_PER_PAYMENT))
	return names


def _payable_invoices(student: str, invoices: list[str]) -> dict:
	"""{invoice: outstanding} after checking each invoice belongs to the student and is payable."""
	rows = {
		row.name: row
		for row in frappe.get_all(
			INVOICE,
			filters={"name": ("in", invoices)},
			fields=["name", "student", "docstatus", "outstanding_amount", "status"],
		)
	}
	payable = {}
	for name in invoices:
		row = rows.get(name)
		if not row or row.student != student:
			frappe.throw(
				_("Invoice {0} was not found for this student.").format(name), frappe.PermissionError
			)
		if row.docstatus != 1:
			frappe.throw(_("Invoice {0} is not open for payment.").format(name))
		if money(row.outstanding_amount) <= 0:
			frappe.throw(_("Invoice {0} is already paid.").format(name))
		payable[name] = money(row.outstanding_amount)
	return payable


def _resolve_overlapping_transaction(tx, gateway, expiry: int, order: dict | None = None) -> None:
	"""An earlier payment covers some of these invoices (or cannot be reused). Decide whether it is abandoned.

	``gateway`` is the adapter of the earlier transaction's own gateway.
	"""
	if not tx.gateway_order_id:
		transactions.mark_failed(tx.name, _("No gateway order was created."), status=TX_EXPIRED)
		return
	if order is None:
		try:
			order = gateway.fetch_order_status(tx.gateway_order_id)
		except GatewayError:
			_throw_in_progress(expiry)

	attempts = order.get("payments") or []
	if order.get("status") == ORDER_CREATED or all(p.get("status") == FAILED for p in attempts):
		# Nothing was paid or is being paid on the earlier order, so it can safely be replaced.
		transactions.mark_failed(
			tx.name,
			_("Replaced by a new payment request."),
			attempts[0] if attempts else None,
			status=TX_EXPIRED,
		)
		return

	status = transactions.settle_with_gateway(
		tx.name, gateway, "checkout", expiry_minutes=expiry, order=order
	)
	if status == TX_SUCCESS:
		# Keep the receipt that was just issued; the error below only stops the new order.
		persist_now()
		frappe.throw(
			_("These dues were just paid (transaction {0}). Please refresh to see your receipt.").format(
				tx.name
			),
			title=_("Already paid"),
		)
	if status in ("Initiated", "Pending"):
		_throw_in_progress(expiry)


def checkout_payload(tx, user: str | None = None) -> dict:
	settings = get_payment_settings()
	gateway = get_gateway(settings, gateway_name=tx.gateway)
	invoices = [row.invoice for row in tx.invoices]
	display_name = settings.merchant_display_name or get_institution_short_name() or _("Hostel Fees")

	logo = get_hostel_setting("institution_logo")
	context = frappe._dict(
		title=display_name,
		description=_("Hostel dues: {0}").format(", ".join(invoices))[:255],
		payer=_payer_prefill(tx.student, user or tx.initiated_by),
		image=get_url(logo) if logo and not str(logo).startswith("/private") else None,
		timeout=_checkout_timeout(tx, settings),
	)
	return {
		"transaction": tx.name,
		"gateway": get_gateway_code(tx.gateway),
		"amount": money(tx.amount),
		"test_mode": cint(tx.test_mode),
		"checkout": gateway.checkout_options(tx, context),
	}


def _checkout_timeout(tx, settings) -> int:
	"""Seconds the checkout may stay open: until shortly before the transaction expires."""
	expiry = cint(settings.transaction_expiry_minutes) or 60
	created = get_datetime(tx.creation) if tx.get("creation") else now_datetime()
	expires_at = add_to_date(created, minutes=expiry)
	remaining = int((expires_at - now_datetime()).total_seconds()) - CHECKOUT_TIMEOUT_MARGIN_SECONDS
	return max(remaining, MIN_CHECKOUT_TIMEOUT_SECONDS)


def _payer_prefill(student: str, user: str | None) -> dict:
	"""Name, e-mail and phone of whoever is paying (the student or the parent signed in)."""
	record = None
	if user and get_student_for_user(user) != student:
		guardian = get_guardian_for_user(user)
		if guardian:
			record = frappe.db.get_value(
				"Hostel Guardian", guardian, ["guardian_name", "email", "mobile_number"], as_dict=True
			)
			if record:
				record.name = record.guardian_name
	if not record:
		record = frappe.db.get_value(
			"Hostel Student", student, ["full_name", "email", "mobile_number"], as_dict=True
		)
		if record:
			record.name = record.full_name
	record = record or frappe._dict()

	email = (record.get("email") or "").strip()
	if email.endswith(".invalid"):
		email = ""
	mobile = "".join(ch for ch in str(record.get("mobile_number") or "") if ch.isdigit())
	contact = f"+91{mobile}" if len(mobile) == 10 else ""
	return {"name": record.get("name") or "", "email": email, "contact": contact}


def confirm_checkout(transaction: str, payload: dict) -> dict:
	"""Confirm a payment from the browser checkout callback. Returns {status, payment, receipt_number}.

	The browser is not trusted: the signature is verified and the payment is read back from the
	gateway before a receipt is issued. Calling this again for a completed payment returns the
	same receipt. Confirmation needs only the gateway's keys, not online payments being enabled:
	money already paid must be receipted even after collection was switched off.
	"""
	payload = frappe._dict(frappe.parse_json(payload) if isinstance(payload, str) else (payload or {}))
	tx = frappe.get_doc(TRANSACTION, transaction)
	assert_can_access_student(tx.student)

	order_id = (payload.get("razorpay_order_id") or payload.get("order_id") or "").strip()
	payment_id = (payload.get("razorpay_payment_id") or payload.get("payment_id") or "").strip()
	signature = (payload.get("razorpay_signature") or payload.get("signature") or "").strip()

	if tx.status == TX_SUCCESS and (not payment_id or payment_id == tx.gateway_payment_id):
		return dict(transactions.result(tx))

	try:
		gateway = get_settlement_gateway(tx.gateway)
	except GatewayNotConfiguredError:
		return _pending(
			tx,
			_(
				"We could not confirm this payment yet. If money was debited, the hostel office will "
				"confirm it. Please quote reference {0}."
			).format(tx.name),
		)

	if gateway.checkout_mode != CHECKOUT_MODAL:
		# Redirect checkouts report through their return URL; here the gateway is simply asked.
		transactions.sync_with_gateway(tx.name, gateway, "checkout")
		return _outcome(tx.name)

	if not (order_id and payment_id and signature):
		frappe.throw(
			_(
				"The payment confirmation is incomplete. Please contact the hostel office if money was debited."
			)
		)

	if order_id != tx.gateway_order_id or not gateway.verify_checkout(order_id, payment_id, signature):
		_report_bad_checkout_signature(tx, order_id, payment_id)

	payment = gateway.fetch_payment(payment_id)

	if tx.status in (TX_SUCCESS, TX_REFUNDED):
		# Another payment on an order that already has its receipt: it is recorded and alerted
		# (never receipted), and an authorised one is left uncaptured so the gateway refunds it.
		if payment.status == CAPTURED:
			return dict(transactions.complete_transaction(tx.name, payment, "checkout"))
		return dict(transactions.result(tx))

	payment = transactions.capture_if_authorised(gateway, tx, payment)

	if payment.status == CAPTURED:
		return dict(transactions.complete_transaction(tx.name, payment, "checkout"))

	if payment.status == FAILED:
		reason = payment.error_description or _("The payment did not go through.")
		if gateway.reusable_orders:
			# The payer can still retry on the same order; it stays open until it expires.
			transactions.mark_pending(tx.name, payment, reason=reason)
		else:
			transactions.mark_failed(tx.name, reason, payment)
		return {
			"status": "Failed",
			"payment": None,
			"receipt_number": None,
			"transaction": tx.name,
			"message": payment.error_description or _("The payment did not go through. You can try again."),
		}

	if payment.status == AUTHORIZED and tx.status in (TX_FAILED, TX_EXPIRED):
		# Not captured on a closed request (see capture_if_authorised): the gateway returns it.
		return {
			"status": tx.status,
			"payment": None,
			"receipt_number": None,
			"transaction": tx.name,
			"message": _(
				"This payment request had already closed, so the amount will be returned to you by the "
				"payment gateway within a few days. Please start a new payment. Reference {0}."
			).format(tx.name),
		}

	transactions.mark_pending(tx.name, payment)
	return _pending(tx, _("Your payment is being processed. The receipt will appear here shortly."))


def confirm_gateway_return(gateway_name: str, fields: dict, source: str = "checkout") -> frappe._dict:
	"""Settle a payment a redirect gateway posted back (the payer's return, or its webhook).

	Returns ``{transaction, status, result}``. ``result`` is ``ok``, ``unknown`` (not one of our
	orders), ``invalid_signature``, ``not_configured``, ``rejected`` (verification refused) or
	``unverified`` (the gateway could not be asked; reconciliation will settle it).
	"""
	fields = {str(key): value for key, value in (fields or {}).items() if key != "cmd"}
	try:
		gateway = get_settlement_gateway(gateway_name)
	except GatewayNotConfiguredError:
		return frappe._dict(transaction=None, status=None, result="not_configured")

	order_id = gateway.return_order_id(fields)
	transaction = (
		frappe.db.get_value(TRANSACTION, {"gateway_order_id": order_id, "gateway": gateway.name}, "name")
		if order_id
		else None
	)

	if not gateway.verify_return(fields):
		_report_bad_return_signature(gateway, transaction, order_id, source)
		return frappe._dict(transaction=transaction, status=None, result="invalid_signature")
	if not transaction:
		return frappe._dict(transaction=None, status=None, result="unknown")

	try:
		with _acting_as_system():
			# The posted status is only a hint: the gateway's own record decides.
			status = transactions.sync_with_gateway(transaction, gateway, source)
		return frappe._dict(transaction=transaction, status=status, result="ok")
	except transactions.PaymentVerificationError:
		return frappe._dict(transaction=transaction, status=TX_FAILED, result="rejected")
	except GatewayError as e:
		# Raised by the status lookup, before anything was written.
		frappe.logger("hostel360").warning(f"{gateway.name} return for {transaction} not verified yet: {e}")
		return frappe._dict(
			transaction=transaction,
			status=frappe.db.get_value(TRANSACTION, transaction, "status"),
			result="unverified",
		)


@contextmanager
def _acting_as_system():
	"""Receipts confirmed by a gateway post are recorded as made by the system, not by "Guest".

	Only the acting user name is switched. ``frappe.set_user`` would also rewrite the session id
	and request data, and the return post comes from the payer's own browser.
	"""
	previous = frappe.session.user
	frappe.session.user = "Administrator"
	try:
		yield
	finally:
		frappe.session.user = previous


def _report_bad_return_signature(gateway, transaction: str | None, order_id: str | None, source: str):
	"""Alert on a rejected post that names one of our transactions; only log any other.

	A forged confirmation, or PayU's own posts after the salt was changed, always carry a txnid of
	ours. A post naming none could not settle anything, and alerting on it would let anyone keep
	the security alert open and hide the posts that matter. Nothing is committed here: the caller
	returns normally and the request commits the alert with it.
	"""
	from hostel360.hostel_fees import alerting

	if not transaction:
		alerting.log_rejected_post(gateway.name, source, "invalid signature")
		return

	alerting.raise_alert(
		f"{gateway.name.lower()}-return-signature",
		_("{0} payment response with an invalid signature was rejected").format(gateway.name),
		severity="Warning",
		category="Security",
		description=_(
			"A {0} post to Hostel360 failed hash verification and was ignored. A single occurrence usually "
			"means the merchant salt was changed; repeated occurrences may be an attempt to forge payment "
			"confirmations. Affected payments are still settled by reconciliation."
		).format(source),
		details={
			"gateway": gateway.name,
			"transaction": transaction,
			"order_id": order_id,
			"source": source,
			"ip": getattr(frappe.local, "request_ip", None),
		},
	)


def _outcome(transaction: str) -> dict:
	tx = frappe.get_doc(TRANSACTION, transaction)
	outcome = dict(transactions.result(tx))
	if tx.status != TX_SUCCESS:
		outcome["message"] = (
			_("The payment did not go through. You can try again.")
			if tx.status in (TX_FAILED, TX_EXPIRED)
			else _("Your payment is being processed. The receipt will appear here shortly.")
		)
	return outcome


def _pending(tx, message: str) -> dict:
	return {
		"status": "Pending",
		"payment": None,
		"receipt_number": None,
		"transaction": tx.name,
		"message": message,
	}


def _report_bad_checkout_signature(tx, order_id: str, payment_id: str):
	from hostel360.hostel_fees import alerting

	frappe.db.set_value(
		TRANSACTION,
		tx.name,
		"failure_reason",
		_("Checkout confirmation with an invalid signature was refused (payment {0}).").format(payment_id)[
			:500
		],
		update_modified=False,
	)
	alerting.raise_alert(
		f"checkout-signature-{tx.name}",
		_("Invalid payment confirmation for {0}").format(tx.name),
		severity="Warning",
		category="Security",
		description=_(
			"A checkout confirmation for transaction {0} carried an invalid signature or a different order. "
			"It was refused. Repeated occurrences may indicate tampering."
		).format(tx.name),
		details={
			"transaction": tx.name,
			"order_id": tx.gateway_order_id,
			"claimed_order_id": order_id,
			"payment_id": payment_id,
			"user": frappe.session.user,
			"ip": getattr(frappe.local, "request_ip", None),
		},
	)
	persist_now()
	raise transactions.PaymentVerificationError(
		_(
			"We could not verify this payment. If money was debited it will be confirmed automatically "
			"or refunded. Please quote reference {0}."
		).format(tx.name)
	)


# -------------------------------------------------------------------------------- documents


def get_receipt_pdf(payment: str) -> bytes:
	doc = frappe.get_doc(PAYMENT, payment)
	assert_can_access_student(doc.student)
	if doc.docstatus != 1:
		frappe.throw(_("Receipt {0} is not available.").format(payment), frappe.DoesNotExistError)
	return render_pdf(doc, "Hostel Fee Receipt")


def get_fee_structure_pdf(student: str) -> bytes:
	assert_can_access_student(student)
	structure = get_applicable_fee_structure(student)
	if not structure:
		frappe.throw(
			_("The fee structure for this student has not been published yet."),
			frappe.DoesNotExistError,
		)
	doc = frappe.get_doc(STRUCTURE, structure)
	student_doc = frappe.db.get_value(
		"Hostel Student",
		student,
		["name", "full_name", "register_number", "current_hostel", "year_of_study", "program"],
		as_dict=True,
	)
	if student_doc and not student_doc.current_hostel:
		# The structure is the one of the hostel the student left; name it on the document.
		student_doc.current_hostel = get_student_hostel(student)
	doc.for_student = student_doc
	return render_pdf(doc, "Hostel Fee Structure")


def get_applicable_fee_structure(student: str) -> str | None:
	"""Most specific active structure for the student's hostel, year of study and programme.

	A structure naming the hostel, year and programme beats one that leaves them blank. Within
	the current academic year (when Hostel Settings names one) the latest due date wins.
	"""
	info = frappe.db.get_value(
		"Hostel Student",
		student,
		["current_hostel", "year_of_study", "program", "academic_year"],
		as_dict=True,
	)
	if not info:
		return None
	# A student who has vacated still owes dues under the structure of the hostel they left.
	info.current_hostel = info.current_hostel or get_student_hostel(student)

	candidates = frappe.get_all(
		STRUCTURE,
		filters={"is_active": 1},
		fields=["name", "academic_year", "hostel", "year_of_study", "program", "due_date", "modified"],
	)
	academic_year = get_current_academic_year() or info.academic_year

	def matches(row) -> bool:
		return (
			(not row.hostel or row.hostel == info.current_hostel)
			and (not row.year_of_study or str(row.year_of_study) == str(info.year_of_study or ""))
			and (not row.program or row.program == info.program)
		)

	def rank(row):
		specificity = bool(row.hostel) + bool(row.year_of_study) + bool(row.program)
		return (
			row.academic_year == academic_year,
			specificity,
			getdate(row.due_date) if row.due_date else getdate("1900-01-01"),
			row.modified,
		)

	matching = sorted((row for row in candidates if matches(row)), key=rank, reverse=True)
	return matching[0].name if matching else None


def render_pdf(doc, print_format: str) -> bytes:
	"""PDF through a standard print format. Portal callers have no print permission of their own;
	access has already been checked against the student.

	The document is printed in the print format's default language, as the desk print view does,
	so a receipt reads the same whoever downloads it. Without this a Tamil-speaking portal user
	got a page mixing the few translated labels into the English wording of the format. Clearing
	the format's default language prints in each user's own language instead.
	"""
	language = frappe.db.get_value("Print Format", print_format, "default_print_language")
	previous = frappe.flags.ignore_print_permissions
	frappe.flags.ignore_print_permissions = True
	try:
		with print_language(language):
			return frappe.get_print(
				doc.doctype, doc.name, print_format=print_format, doc=doc, as_pdf=True, no_letterhead=1
			)
	finally:
		frappe.flags.ignore_print_permissions = previous
