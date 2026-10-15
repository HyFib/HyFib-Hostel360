"""Fee services (design section 5).

These functions hold the fee rules used by the portal API, the mess billing run, the data
migration and the desk helpers. They run with elevated document permissions, so every function
that acts for a person first checks that the person may act for the student.
"""

import frappe
from frappe import _
from frappe.model.naming import make_autoname
from frappe.utils import cint, get_url, getdate, nowdate

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
	TX_SUCCESS,
)
from hostel360.hostel_fees.gateways import (
	GatewayError,
	GatewayNotConfiguredError,
	get_gateway,
	get_gateway_code,
	to_minor_units,
)
from hostel360.hostel_fees.gateways.base import CAPTURED, FAILED, ORDER_CREATED
from hostel360.hostel_fees.utils import (
	get_current_academic_year,
	get_hostel_setting,
	get_institution_short_name,
	get_payment_settings,
	money,
	persist_now,
)
from hostel360.utils.context import assert_can_access_student, get_guardian_for_user, get_student_for_user

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
		"payment_enabled": bool(get_payment_settings().enabled),
	}


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

	Returns ``{transaction, gateway, checkout}`` where ``checkout`` holds the browser checkout
	options. Asking again for exactly the same invoices while a payment is still open returns the
	same order, so a double tap or a page refresh never creates a second charge.
	"""
	assert_can_access_student(student, user)
	invoices = _clean_invoice_list(invoices)
	settings = get_payment_settings()
	gateway = get_gateway(settings)

	requested = _payable_invoices(student, invoices)
	expiry = cint(settings.transaction_expiry_minutes) or 60

	# Resolve earlier attempts before taking invoice locks: transactions are always locked
	# before invoices, the same order the completion path uses.
	live = transactions.get_live_transactions(invoices, expiry)
	for tx in live.values():
		if tx.student == student and tx.invoices == requested:
			return checkout_payload(frappe.get_doc(TRANSACTION, tx.name), user)
	for tx in live.values():
		_resolve_overlapping_transaction(tx, gateway, expiry)

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


def _resolve_overlapping_transaction(tx, gateway, expiry: int) -> None:
	"""An earlier, different payment covers some of these invoices. Decide whether it is abandoned."""
	in_progress = _(
		"A payment for some of these dues is already in progress. Please complete it, or try again "
		"after {0} minutes."
	).format(expiry)

	if not tx.gateway_order_id:
		transactions.mark_failed(tx.name, _("No gateway order was created."), status=TX_EXPIRED)
		return
	try:
		order = gateway.fetch_order_status(tx.gateway_order_id)
	except GatewayError:
		frappe.throw(in_progress, title=_("Payment in progress"))

	attempts = order.get("payments") or []
	if order.get("status") == ORDER_CREATED or all(p.get("status") == FAILED for p in attempts):
		# Nothing was paid or is being paid on the earlier order, so it can safely be replaced.
		transactions.mark_failed(
			tx.name,
			_("Replaced by a new payment request for a different set of dues."),
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
		frappe.throw(in_progress, title=_("Payment in progress"))


def checkout_payload(tx, user: str | None = None) -> dict:
	settings = get_payment_settings()
	gateway = get_gateway(settings)
	invoices = [row.invoice for row in tx.invoices]
	display_name = settings.merchant_display_name or get_institution_short_name() or _("Hostel Fees")

	checkout = {
		"key": gateway.checkout_key,
		"order_id": tx.gateway_order_id,
		"amount": to_minor_units(tx.amount),
		"currency": tx.currency or CURRENCY,
		"name": display_name,
		"description": _("Hostel dues: {0}").format(", ".join(invoices))[:255],
		"prefill": _payer_prefill(tx.student, user or tx.initiated_by),
		"notes": {"transaction": tx.name, "student": tx.student},
	}
	logo = get_hostel_setting("institution_logo")
	if logo and not str(logo).startswith("/private"):
		checkout["image"] = get_url(logo)

	return {
		"transaction": tx.name,
		"gateway": get_gateway_code(tx.gateway),
		"amount": money(tx.amount),
		"test_mode": cint(tx.test_mode),
		"checkout": checkout,
	}


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
	same receipt.
	"""
	payload = frappe._dict(frappe.parse_json(payload) if isinstance(payload, str) else (payload or {}))
	tx = frappe.get_doc(TRANSACTION, transaction)
	assert_can_access_student(tx.student)

	if tx.status == TX_SUCCESS:
		return dict(transactions.result(tx))

	order_id = (payload.get("razorpay_order_id") or payload.get("order_id") or "").strip()
	payment_id = (payload.get("razorpay_payment_id") or payload.get("payment_id") or "").strip()
	signature = (payload.get("razorpay_signature") or payload.get("signature") or "").strip()
	if not (order_id and payment_id and signature):
		frappe.throw(
			_(
				"The payment confirmation is incomplete. Please contact the hostel office if money was debited."
			)
		)

	try:
		gateway = get_gateway()
	except GatewayNotConfiguredError:
		return _pending(tx, _("Your payment will be confirmed automatically within a few minutes."))

	if order_id != tx.gateway_order_id or not gateway.verify_checkout(order_id, payment_id, signature):
		_report_bad_checkout_signature(tx, order_id, payment_id)

	payment = gateway.fetch_payment(payment_id)
	payment = transactions.capture_if_authorised(gateway, tx, payment)

	if payment.status == CAPTURED:
		return dict(transactions.complete_transaction(tx.name, payment, "checkout"))

	if payment.status == FAILED:
		transactions.mark_failed(
			tx.name, payment.error_description or _("The payment did not go through."), payment
		)
		return {
			"status": "Failed",
			"payment": None,
			"receipt_number": None,
			"transaction": tx.name,
			"message": payment.error_description or _("The payment did not go through. You can try again."),
		}

	transactions.mark_pending(tx.name, payment)
	return _pending(tx, _("Your payment is being processed. The receipt will appear here shortly."))


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
	access has already been checked against the student."""
	previous = frappe.flags.ignore_print_permissions
	frappe.flags.ignore_print_permissions = True
	try:
		return frappe.get_print(
			doc.doctype, doc.name, print_format=print_format, doc=doc, as_pdf=True, no_letterhead=1
		)
	finally:
		frappe.flags.ignore_print_permissions = previous
