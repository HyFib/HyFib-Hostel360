"""Record builders shared by the fee module's tests.

Every builder creates fresh records with unique names so tests never depend on each other or on
data created by other modules' tests.
"""

import hashlib
import hmac
import json
import random
import time
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from hostel360.hostel_fees.constants import INVOICE, SETTINGS, STRUCTURE, TRANSACTION

# Linked DocTypes belong to other modules; fee tests build exactly the records they need.
IGNORE_TEST_RECORD_DEPENDENCIES = [
	"Hostel Student",
	"Hostel",
	"Hostel Academic Year",
	"Academic Program",
	"User",
	"DocType",
	"Hostel Fee Head",
	"Hostel Fee Structure",
	"Hostel Fee Invoice",
	"Hostel Payment",
	"Hostel Payment Transaction",
]

TEST_KEY_ID = "rzp_test_H360UnitTests"
LIVE_KEY_ID = "rzp_live_H360UnitTests"
TEST_KEY_SECRET = "unit-test-key-secret"  # noqa: S105
TEST_WEBHOOK_SECRET = "unit-test-webhook-secret"  # noqa: S105
PAYU_KEY = "h360UTkey"
PAYU_SALT = "h360-unit-test-salt"


def unique(prefix: str = "") -> str:
	return f"{prefix}{frappe.generate_hash(length=8).upper()}"


def random_mobile() -> str:
	return "9" + "".join(random.choice("0123456789") for _ in range(9))


def make_hostel(gender: str = "Male") -> str:
	code = unique("T")[:8]
	doc = frappe.get_doc(
		{
			"doctype": "Hostel",
			"hostel_name": f"Test Hostel {code}",
			"hostel_code": code,
			"gender": gender,
			"is_active": 1,
		}
	)
	doc.flags.ignore_mandatory = True
	doc.insert(ignore_permissions=True)
	return doc.name


def make_academic_year(name: str = "2026-2027") -> str:
	if not frappe.db.exists("Hostel Academic Year", name):
		start = name.split("-")[0]
		doc = frappe.get_doc(
			{
				"doctype": "Hostel Academic Year",
				"academic_year": name,
				"start_date": f"{start}-06-01",
				"end_date": f"{int(start) + 1}-05-31",
			}
		)
		doc.flags.ignore_mandatory = True
		doc.insert(ignore_permissions=True)
	return name


def make_program() -> str:
	suffix = unique()
	department = frappe.get_doc(
		{
			"doctype": "Academic Department",
			"department_name": f"Test Department {suffix}",
			"department_code": suffix[:6],
		}
	).insert(ignore_permissions=True)
	program = frappe.get_doc(
		{
			"doctype": "Academic Program",
			"program_name": f"Test Programme {suffix}",
			"department": department.name,
			"degree_level": "UG",
			"duration_years": 4,
		}
	)
	program.flags.ignore_mandatory = True
	program.insert(ignore_permissions=True)
	return program.name


def make_student(
	hostel: str | None = None,
	year_of_study: str = "1",
	program: str | None = None,
	status: str = "Active",
	validation_status: str = "Validated",
	email: str | None = None,
) -> str:
	suffix = unique()
	doc = frappe.new_doc("Hostel Student")
	values = {
		"register_number": f"FT{suffix}",
		"first_name": "Fee",
		"last_name": f"Tester {suffix}",
		"gender": "Male",
		"mobile_number": random_mobile(),
		"email": email or f"fee.{suffix.lower()}@example.com",
		"year_of_study": year_of_study,
		"program": program,
		"status": "Active",
	}
	for fieldname, value in values.items():
		if doc.meta.has_field(fieldname):
			doc.set(fieldname, value)
	doc.flags.ignore_mandatory = True
	doc.insert(ignore_permissions=True)

	# Validation and room allotment belong to other modules; set their outcome directly.
	frappe.db.set_value(
		"Hostel Student",
		doc.name,
		{
			"status": status,
			"validation_status": validation_status,
			"current_hostel": hostel,
			"full_name": f"Fee Tester {suffix}",
		},
		update_modified=False,
	)
	return doc.name


def make_scoped_user(hostel: str, role: str | None = None) -> str:
	"""A desk user limited to ``hostel`` through a User Permission, as Hostel Staff sync sets up."""
	from hostel360.setup.roles import OFFICE_ROLE

	email = f"{unique('fee.staff.').lower()}@example.com"
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": "Fee Counter",
			"user_type": "System User",
			"send_welcome_email": 0,
			"roles": [{"role": role or OFFICE_ROLE}],
		}
	)
	user.flags.no_welcome_mail = True
	user.insert(ignore_permissions=True)
	frappe.get_doc(
		{
			"doctype": "User Permission",
			"user": email,
			"allow": "Hostel",
			"for_value": hostel,
			"apply_to_all_doctypes": 1,
		}
	).insert(ignore_permissions=True)
	return email


def move_student(student: str, hostel: str | None, last_hostel: str | None = None) -> None:
	"""Record the outcome of a transfer or vacating (allotments belong to the people module)."""
	values = {"current_hostel": hostel}
	if frappe.get_meta("Hostel Student").has_field("last_hostel"):
		values["last_hostel"] = last_hostel or hostel
	frappe.db.set_value("Hostel Student", student, values, update_modified=False)


def make_invoice(
	student: str,
	amount: float = 1000,
	due_date: str | None = None,
	posting_date: str | None = None,
	items: list[dict] | None = None,
	submit: bool = True,
	invoice_type: str = "Hostel Fee",
) -> "frappe.model.document.Document":
	from hostel360.hostel_fees.services import create_invoice

	name = create_invoice(
		student,
		items or [{"fee_head": "Hostel Rent", "amount": amount}],
		invoice_type,
		due_date or add_days(nowdate(), 15),
		posting_date=posting_date,
		submit=submit,
	)
	return frappe.get_doc(INVOICE, name)


def make_payment(student: str, allocations: list[tuple[str, float]], mode: str = "Cash", **values):
	doc = frappe.new_doc("Hostel Payment")
	doc.update(
		{
			"student": student,
			"mode_of_payment": mode,
			"payment_date": nowdate(),
			**values,
		}
	)
	for invoice, amount in allocations:
		doc.append("allocations", {"invoice": invoice, "allocated_amount": amount})
	doc.insert(ignore_permissions=True)
	return doc


def configure_gateway(
	enabled: int = 1,
	webhook_secret: str | None = TEST_WEBHOOK_SECRET,
	expiry: int = 60,
	live: bool = False,
	gateway: str = "Razorpay",
	test_payers: list[str] | None = None,
	key_secret: str | None = TEST_KEY_SECRET,
	payu_salt: str | None = PAYU_SALT,
):
	"""Hostel Payment Settings for tests. ``live`` uses live-mode credentials (real money)."""
	settings = frappe.get_single(SETTINGS)
	settings.update(
		{
			"enabled": enabled,
			"gateway": gateway,
			"test_mode": 0 if live else 1,
			"key_id": LIVE_KEY_ID if live else TEST_KEY_ID,
			"key_secret": key_secret or "",
			"webhook_secret": webhook_secret or "",
			"payu_merchant_key": PAYU_KEY,
			"payu_merchant_salt": payu_salt or "",
			"transaction_expiry_minutes": expiry,
			"merchant_display_name": "Test Hostels",
		}
	)
	settings.set("test_payers", [{"student": student} for student in test_payers or []])
	settings.flags.ignore_permissions = True
	settings.save()
	frappe.clear_document_cache(SETTINGS, SETTINGS)
	return settings


def checkout_signature(order_id: str, payment_id: str, secret: str = TEST_KEY_SECRET) -> str:
	return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


def webhook_signature(body: bytes, secret: str = TEST_WEBHOOK_SECRET) -> str:
	return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def payment_entity(
	order_id: str,
	amount_paise: int,
	payment_id: str | None = None,
	status: str = "captured",
	method: str = "upi",
	**extra,
) -> dict:
	return {
		"id": payment_id or unique("pay_"),
		"entity": "payment",
		"amount": amount_paise,
		"currency": "INR",
		"status": status,
		"order_id": order_id,
		"method": method,
		"captured": status == "captured",
		"email": "payer@example.com",
		"contact": "+919800000000",
		"created_at": int(time.time()) - 60,
		**extra,
	}


def webhook_body(event: str, payment: dict, order: dict | None = None) -> bytes:
	payload = {"payment": {"entity": payment}}
	if order:
		payload["order"] = {"entity": order}
	return json.dumps(
		{
			"entity": "event",
			"account_id": "acc_test",
			"event": event,
			"contains": list(payload),
			"payload": payload,
		}
	).encode()


class FakeResponse:
	"""Stand-in for requests.Response."""

	def __init__(self, status_code: int = 200, data: dict | None = None):
		self.status_code = status_code
		self._data = data if data is not None else {}
		self.reason = "OK" if status_code < 400 else "Error"

	def json(self):
		return self._data


class FakeRazorpay:
	"""Minimal in-memory Razorpay API used through ``requests.request``.

	Records every call in ``calls`` so tests can assert the exact payloads sent.
	"""

	def __init__(self):
		self.calls = []
		self.orders = {}
		self.payments = {}
		self.fail_next = None
		# {order_id: response} for orders the API cannot read
		self.unreadable_orders = {}

	def __call__(self, method, url, auth=None, json=None, params=None, timeout=None, headers=None):
		self.calls.append(
			{"method": method, "url": url, "auth": auth, "json": json, "params": params, "timeout": timeout}
		)
		if self.fail_next:
			failure, self.fail_next = self.fail_next, None
			if isinstance(failure, Exception):
				raise failure
			return failure

		path = url.split("/v1", 1)[1]
		if method == "POST" and path == "/orders":
			order_id = unique("order_")
			self.orders[order_id] = {
				"id": order_id,
				"entity": "order",
				"amount": json["amount"],
				"currency": json["currency"],
				"receipt": json["receipt"],
				"status": "created",
				"notes": json.get("notes") or {},
			}
			return FakeResponse(200, self.orders[order_id])
		if method == "GET" and path.startswith("/orders/") and path.endswith("/payments"):
			order_id = path.split("/")[2]
			if order_id in self.unreadable_orders:
				return self.unreadable_orders[order_id]
			items = [p for p in self.payments.values() if p["order_id"] == order_id]
			return FakeResponse(200, {"entity": "collection", "count": len(items), "items": items})
		if method == "GET" and path.startswith("/payments/"):
			payment = self.payments.get(path.split("/")[2])
			if not payment:
				return FakeResponse(
					400,
					{"error": {"code": "BAD_REQUEST_ERROR", "description": "The id provided does not exist"}},
				)
			return FakeResponse(200, payment)
		if method == "POST" and path.startswith("/payments/") and path.endswith("/capture"):
			payment = self.payments[path.split("/")[2]]
			payment.update(status="captured", captured=True)
			return FakeResponse(200, payment)
		if method == "GET" and path == "/orders":
			return FakeResponse(200, {"entity": "collection", "count": 0, "items": []})
		return FakeResponse(404, {"error": {"code": "NOT_FOUND", "description": "Unknown endpoint"}})

	def pay(self, order_id: str, status: str = "captured", amount: int | None = None, **extra) -> dict:
		order = self.orders[order_id]
		payment = payment_entity(
			order_id, amount if amount is not None else order["amount"], status=status, **extra
		)
		self.payments[payment["id"]] = payment
		return payment


class FeeTestCase(IntegrationTestCase):
	"""Base class: records queued notifications and raised alerts instead of sending them."""

	def setUp(self):
		super().setUp()
		frappe.flags.hostel360_migration = False
		self.notifications = self._patch("hostel360.hostel_fees.notifications._queue")
		self.alerts = self._patch("hostel360.hostel_fees.alerting.raise_alert")
		self.resolved = self._patch("hostel360.hostel_fees.alerting.resolve_alert")

	def tearDown(self):
		frappe.flags.hostel360_migration = False
		super().tearDown()

	def _patch(self, target):
		patcher = patch(target)
		mocked = patcher.start()
		self.addCleanup(patcher.stop)
		return mocked

	def notified(self, event: str, name: str | None = None) -> list:
		return [
			call.kwargs
			for call in self.notifications.call_args_list
			if call.kwargs.get("notification_event") == event
			and (name is None or call.kwargs.get("reference_name") == name)
		]

	def alert_keys(self) -> list[str]:
		return [call.args[0] if call.args else call.kwargs.get("key") for call in self.alerts.call_args_list]

	def alerts_in(self, category: str) -> list:
		return [call for call in self.alerts.call_args_list if call.kwargs.get("category") == category]

	# Records the site already holds (seeded demo data, an earlier committed run) must not decide
	# a test's outcome. These take them out of play for one test; the changes are undone after it.

	def deactivate_site_structures(self) -> None:
		"""Deactivate every active fee structure on the site.

		A structure that leaves hostel, year and programme blank applies to every student, so one
		already on the site would otherwise be found for any student a test creates.
		"""
		active = frappe.get_all(STRUCTURE, filters={"is_active": 1}, pluck="name")
		if not active:
			return
		frappe.db.set_value(STRUCTURE, {"name": ["in", active]}, "is_active", 0, update_modified=False)
		self.addCleanup(
			frappe.db.set_value, STRUCTURE, {"name": ["in", active]}, "is_active", 1, update_modified=False
		)

	def hide_site_transactions(self, gateway: str) -> None:
		"""Make the site look as if it never took a payment through ``gateway``."""
		alias = f"{gateway} (hidden by test)"
		frappe.db.set_value(TRANSACTION, {"gateway": gateway}, "gateway", alias, update_modified=False)
		self.addCleanup(
			frappe.db.set_value, TRANSACTION, {"gateway": alias}, "gateway", gateway, update_modified=False
		)


# -- PayU ------------------------------------------------------------------------------------
# Hashes are computed here from PayU's published formulas, independently of the adapter, so the
# tests check the adapter against the specification rather than against itself.

UDFS = tuple(f"udf{i}" for i in range(1, 11))


def payu_sha512(*parts) -> str:
	return hashlib.sha512("|".join(str(part) for part in parts).encode()).hexdigest()


def payu_request_hash(fields: dict, salt: str = PAYU_SALT) -> str:
	return payu_sha512(
		fields["key"],
		fields["txnid"],
		fields["amount"],
		fields["productinfo"],
		fields["firstname"],
		fields["email"],
		*(fields.get(name, "") for name in UDFS),
		salt,
	)


def payu_response_hash(fields: dict, salt: str = PAYU_SALT) -> str:
	parts = [
		salt,
		fields["status"],
		*(fields.get(name, "") for name in reversed(UDFS)),
		fields["email"],
		fields["firstname"],
		fields["productinfo"],
		fields["amount"],
		fields["txnid"],
		fields["key"],
	]
	if fields.get("additionalCharges"):
		parts.insert(0, fields["additionalCharges"])
	return payu_sha512(*parts)


class FakePayU:
	"""Minimal in-memory PayU merchant API (``postservice.php?form=2``) used through ``requests.post``.

	Rejects calls whose command hash is wrong, as PayU does, and records every call in ``calls``.
	"""

	def __init__(self, key: str = PAYU_KEY, salt: str = PAYU_SALT):
		self.key = key
		self.salt = salt
		self.calls = []
		self.transactions = {}
		self.fail_next = None

	def __call__(self, url, data=None, timeout=None, headers=None):
		data = dict(data or {})
		self.calls.append({"url": url, "data": data, "timeout": timeout})
		if self.fail_next:
			failure, self.fail_next = self.fail_next, None
			if isinstance(failure, Exception):
				raise failure
			return failure
		expected = payu_sha512(data.get("key"), data.get("command"), data.get("var1"), self.salt)
		if data.get("key") != self.key or data.get("hash") != expected:
			return FakeResponse(200, {"status": 0, "msg": "Invalid Hash."})
		var1 = data.get("var1")
		if data.get("command") == "verify_payment":
			record = self.transactions.get(var1) or {"mihpayid": "Not Found", "status": "Not Found"}
			found = 0 if record.get("status") == "Not Found" else 1
			return FakeResponse(
				200,
				{
					"status": found,
					"msg": f"{found} out of 1 Transactions Fetched Successfully",
					"transaction_details": {var1: record},
				},
			)
		if data.get("command") == "check_payment":
			record = next((t for t in self.transactions.values() if t["mihpayid"] == var1), None)
			if not record:
				return FakeResponse(
					200, {"status": 0, "msg": "No transaction found", "transaction_details": {}}
				)
			return FakeResponse(
				200, {"status": 1, "msg": "Transaction Fetched", "transaction_details": record}
			)
		return FakeResponse(200, {"status": 0, "msg": "Unknown command"})

	def pay(
		self,
		checkout: dict,
		status: str = "success",
		unmapped: str = "captured",
		amount: str | None = None,
		**extra,
	) -> dict:
		"""Record a PayU transaction for the posted checkout form; returns the verify API record."""
		fields = checkout["fields"]
		record = {
			"mihpayid": str(random.randint(10**11, 10**12 - 1)),
			"txnid": fields["txnid"],
			"amt": amount or fields["amount"],
			"transaction_amount": amount or fields["amount"],
			"status": status,
			"unmappedstatus": unmapped,
			"mode": "UPI",
			"bankcode": "UPI",
			"PG_TYPE": "UPI-PG",
			"bank_ref_num": str(random.randint(10**9, 10**10 - 1)),
			"productinfo": fields["productinfo"],
			"firstname": fields["firstname"],
			"udf1": fields.get("udf1", ""),
			"error_code": "E000" if status == "success" else "E308",
			"error_Message": "NO ERROR" if status == "success" else "Transaction declined",
			"addedon": time.strftime("%Y-%m-%d %H:%M:%S"),
			**extra,
		}
		self.transactions[fields["txnid"]] = record
		return record

	def return_fields(
		self, checkout: dict, record: dict | None = None, status: str | None = None, **overrides
	) -> dict:
		"""What PayU posts to surl/furl for ``record``, signed with the merchant salt."""
		sent = checkout["fields"]
		record = record or {}
		fields = {
			"mihpayid": record.get("mihpayid", "403993715500000000"),
			"mode": record.get("mode", "UPI"),
			"status": status or record.get("status", "success"),
			"unmappedstatus": record.get("unmappedstatus", "captured"),
			"key": sent["key"],
			"txnid": sent["txnid"],
			"amount": sent["amount"],
			"productinfo": sent["productinfo"],
			"firstname": sent["firstname"],
			"email": sent["email"],
			"phone": sent["phone"],
			"udf1": sent.get("udf1", ""),
			"field9": record.get("error_Message", ""),
			"error": record.get("error_code", "E000"),
			"error_Message": record.get("error_Message", "NO ERROR"),
			"bank_ref_num": record.get("bank_ref_num", ""),
		}
		fields.update(overrides)
		fields["hash"] = payu_response_hash(fields, self.salt)
		return fields
