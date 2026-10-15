# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import set_user
from frappe.utils import add_days, cint, getdate, nowdate

from hostel360.hostel_fees.constants import INVOICE, SETTINGS
from hostel360.hostel_fees.reminders import send_fee_reminders
from hostel360.hostel_fees.services import cancel_invoice, create_invoice, get_fee_summary
from hostel360.hostel_fees.tasks import mark_overdue_invoices
from hostel360.hostel_fees.tests import fixtures as fee_fixtures
from hostel360.hostel_fees.tests.fixtures import (
	FeeTestCase,
	make_hostel,
	make_invoice,
	make_payment,
	make_scoped_user,
	make_student,
	move_student,
)

# Read by the Frappe test runner: fee tests build every linked record they need themselves.
IGNORE_TEST_RECORD_DEPENDENCIES = fee_fixtures.IGNORE_TEST_RECORD_DEPENDENCIES


class TestHostelFeeInvoice(FeeTestCase):
	def setUp(self):
		super().setUp()
		self.hostel = make_hostel()
		self.student = make_student(hostel=self.hostel)
		frappe.db.set_single_value(
			SETTINGS,
			{
				"send_fee_reminders": 1,
				"reminder_days_before_due": 3,
				"reminder_interval_days": 3,
				"max_reminders_per_invoice": 0,
			},
		)
		frappe.clear_document_cache(SETTINGS, SETTINGS)

	# -- totals and status -------------------------------------------------------------------

	def test_totals_hostel_and_status(self):
		invoice = make_invoice(
			self.student,
			items=[
				{"fee_head": "Hostel Rent", "amount": 12000},
				{"fee_head": "Establishment Charges", "amount": 3500.5},
			],
		)
		self.assertEqual(invoice.docstatus, 1)
		self.assertEqual(invoice.total_amount, 15500.5)
		self.assertEqual(invoice.paid_amount, 0)
		self.assertEqual(invoice.outstanding_amount, 15500.5)
		self.assertEqual(invoice.status, "Unpaid")
		self.assertEqual(invoice.hostel, self.hostel)

	def test_validation_rules(self):
		with self.assertRaises(frappe.ValidationError):
			make_invoice(self.student, items=[{"fee_head": "Hostel Rent", "amount": 0}])
		with self.assertRaises(frappe.ValidationError):
			make_invoice(self.student, due_date=add_days(nowdate(), -5))
		with self.assertRaises(frappe.ValidationError):
			create_invoice(self.student, [{"fee_head": "Fine", "amount": 10}], "Penalty", nowdate())

		frappe.db.set_value("Hostel Fee Head", "Other", "is_active", 0)
		with self.assertRaises(frappe.ValidationError):
			make_invoice(self.student, items=[{"fee_head": "Other", "amount": 100}])
		frappe.db.set_value("Hostel Fee Head", "Other", "is_active", 1)

	def test_status_transitions_with_payments(self):
		invoice = make_invoice(self.student, 1000)

		first = make_payment(self.student, [(invoice.name, 400)])
		first.submit()
		invoice.reload()
		self.assertEqual(
			(invoice.status, invoice.paid_amount, invoice.outstanding_amount), ("Partly Paid", 400, 600)
		)

		second = make_payment(self.student, [(invoice.name, 600)], mode="UPI", reference_no="UTR123")
		second.submit()
		invoice.reload()
		self.assertEqual((invoice.status, invoice.outstanding_amount), ("Paid", 0))

		second.cancel()
		invoice.reload()
		self.assertEqual((invoice.status, invoice.outstanding_amount), ("Partly Paid", 600))

		first.cancel()
		invoice.reload()
		self.assertEqual((invoice.status, invoice.paid_amount), ("Unpaid", 0))

	def test_past_due_invoice_is_overdue_and_due_date_can_be_extended(self):
		invoice = make_invoice(
			self.student, 500, posting_date=add_days(nowdate(), -40), due_date=add_days(nowdate(), -10)
		)
		self.assertEqual(invoice.status, "Overdue")

		invoice.due_date = add_days(nowdate(), 7)
		invoice.save()
		self.assertEqual(frappe.db.get_value(INVOICE, invoice.name, "status"), "Unpaid")

	def test_mark_overdue_invoices(self):
		due_soon = make_invoice(self.student, 700)
		paid = make_invoice(self.student, 300)
		make_payment(self.student, [(paid.name, 300)]).submit()

		yesterday = add_days(nowdate(), -1)
		for name in (due_soon.name, paid.name):
			frappe.db.set_value(
				INVOICE, name, {"due_date": yesterday, "posting_date": add_days(nowdate(), -20)}
			)

		mark_overdue_invoices()
		self.assertEqual(frappe.db.get_value(INVOICE, due_soon.name, "status"), "Overdue")
		self.assertEqual(frappe.db.get_value(INVOICE, paid.name, "status"), "Paid")

	def test_cancel_rules(self):
		invoice = make_invoice(self.student, 800)
		make_payment(self.student, [(invoice.name, 100)]).submit()
		with self.assertRaises(frappe.ValidationError):
			cancel_invoice(invoice.name)

		unpaid = make_invoice(self.student, 900)
		cancel_invoice(unpaid.name)
		self.assertEqual(frappe.db.get_value(INVOICE, unpaid.name, ["docstatus", "status"]), (2, "Cancelled"))
		cancel_invoice(unpaid.name)  # already cancelled: no error

		draft = make_invoice(self.student, 900, submit=False)
		cancel_invoice(draft.name)
		self.assertFalse(frappe.db.exists(INVOICE, draft.name))

	def test_paid_amount_cannot_be_forged_outside_migration(self):
		invoice = frappe.get_doc(
			{
				"doctype": INVOICE,
				"student": self.student,
				"invoice_type": "Other",
				"due_date": add_days(nowdate(), 5),
				"items": [{"fee_head": "Other", "amount": 1000}],
				"paid_amount": 1000,
				"migrated_paid_amount": 1000,
			}
		).insert()
		invoice.submit()
		self.assertEqual(
			(invoice.paid_amount, invoice.outstanding_amount, invoice.status), (0, 1000, "Unpaid")
		)

	def test_migration_accepts_historic_paid_invoices(self):
		frappe.flags.hostel360_migration = True
		invoice = frappe.get_doc(
			{
				"doctype": INVOICE,
				"student": self.student,
				"invoice_type": "Hostel Fee",
				"posting_date": "2024-07-01",
				"due_date": "2024-06-15",
				"items": [{"fee_head": "Hostel Rent", "amount": 1000}],
				"paid_amount": 600,
			}
		).insert()
		invoice.submit()
		frappe.flags.hostel360_migration = False

		self.assertEqual(invoice.migrated_paid_amount, 600)
		self.assertEqual(
			(invoice.paid_amount, invoice.outstanding_amount, invoice.status), (600, 400, "Overdue")
		)
		self.assertFalse(self.notified("fee_due_published", invoice.name))

		# A later receipt adds to the migrated amount instead of replacing it.
		make_payment(self.student, [(invoice.name, 400)]).submit()
		invoice.reload()
		self.assertEqual((invoice.paid_amount, invoice.status), (1000, "Paid"))

	def test_due_notification(self):
		invoice = make_invoice(self.student, 1000)
		sent = self.notified("fee_due_published", invoice.name)
		self.assertEqual(len(sent), 1)
		self.assertEqual(sent[0]["student"], self.student)
		self.assertTrue(sent[0]["include_guardians"])
		self.assertEqual(sent[0]["context"]["amount"], 1000)

		mess_bill = make_invoice(
			self.student, 2100, invoice_type="Mess Bill", items=[{"fee_head": "Mess Charges", "amount": 2100}]
		)
		self.assertFalse(self.notified("fee_due_published", mess_bill.name))

	def test_fee_summary(self):
		overdue = make_invoice(
			self.student, 500, posting_date=add_days(nowdate(), -30), due_date=add_days(nowdate(), -2)
		)
		upcoming = make_invoice(self.student, 1500, due_date=add_days(nowdate(), 10))
		make_payment(self.student, [(upcoming.name, 500)]).submit()

		summary = get_fee_summary(self.student)
		self.assertEqual(summary["outstanding"], 1500)
		self.assertEqual(summary["overdue"], 500)
		self.assertEqual(getdate(summary["next_due_date"]), getdate(add_days(nowdate(), 10)))
		self.assertEqual({row.name for row in summary["invoices"]}, {overdue.name, upcoming.name})
		self.assertEqual(len(summary["payments"]), 1)
		self.assertTrue(all(row["items"] for row in summary["invoices"]))

	# -- reminders ---------------------------------------------------------------------------

	def reminders_for(self, student: str | None = None) -> list[dict]:
		return [k for k in self.notified("fee_reminder") if k["student"] == (student or self.student)]

	def reminded_invoices(self, student: str | None = None) -> set[str]:
		return {row["invoice"] for k in self.reminders_for(student) for row in k["context"]["invoices"]}

	def test_one_reminder_per_student_covers_due_soon_and_overdue(self):
		due_soon = make_invoice(self.student, 100, due_date=add_days(nowdate(), 2))
		far_off = make_invoice(self.student, 100, due_date=add_days(nowdate(), 20))
		overdue = make_invoice(
			self.student, 250, posting_date=add_days(nowdate(), -30), due_date=add_days(nowdate(), -5)
		)
		paid = make_invoice(self.student, 100, due_date=add_days(nowdate(), 1))
		make_payment(self.student, [(paid.name, 100)]).submit()

		self.notifications.reset_mock()
		send_fee_reminders()
		reminders = self.reminders_for()
		self.assertEqual(len(reminders), 1, "one message for the family, not one per invoice")
		self.assertEqual(self.reminded_invoices(), {due_soon.name, overdue.name})

		reminder = reminders[0]
		self.assertEqual(reminder["reference_name"], overdue.name)
		context = reminder["context"]
		self.assertEqual((context["outstanding"], context["invoice_count"]), (350, 2))
		self.assertTrue(context["is_overdue"])
		self.assertEqual(context["days_overdue"], 5)
		self.assertIn(due_soon.name, context["invoice"])
		self.assertEqual(frappe.db.get_value(INVOICE, overdue.name, "reminder_count"), 1)
		self.assertEqual(frappe.db.get_value(INVOICE, due_soon.name, "reminder_count"), 1)
		self.assertEqual(frappe.db.get_value(INVOICE, far_off.name, "reminder_count"), 0)

	def test_reminders_are_throttled_per_family(self):
		invoice = make_invoice(self.student, 100, due_date=add_days(nowdate(), 1))

		self.notifications.reset_mock()
		send_fee_reminders()
		self.assertIn(invoice.name, self.reminded_invoices())

		self.notifications.reset_mock()
		send_fee_reminders()
		self.assertFalse(self.reminders_for(), "reminded twice on the same day")

		frappe.db.set_value(INVOICE, invoice.name, "last_reminder_on", add_days(nowdate(), -2))
		self.notifications.reset_mock()
		send_fee_reminders()
		self.assertFalse(self.reminders_for(), "reminded within three days")

		frappe.db.set_value(INVOICE, invoice.name, "last_reminder_on", add_days(nowdate(), -3))
		self.notifications.reset_mock()
		send_fee_reminders()
		self.assertIn(invoice.name, self.reminded_invoices())
		self.assertEqual(frappe.db.get_value(INVOICE, invoice.name, "reminder_count"), 2)

	def test_invoices_with_different_due_dates_do_not_multiply_messages(self):
		# Reminded yesterday for one bill; a second bill falling due today must wait for the interval.
		earlier = make_invoice(self.student, 100, due_date=add_days(nowdate(), 1))
		frappe.db.set_value(
			INVOICE, earlier.name, {"last_reminder_on": add_days(nowdate(), -1), "reminder_count": 1}
		)
		make_invoice(self.student, 200, due_date=add_days(nowdate(), 2))

		self.notifications.reset_mock()
		send_fee_reminders()
		self.assertFalse(self.reminders_for())

	def test_reminder_limit_and_switch(self):
		capped = make_invoice(self.student, 100, due_date=add_days(nowdate(), 1))
		frappe.db.set_value(INVOICE, capped.name, "reminder_count", 2)

		frappe.db.set_single_value(SETTINGS, "max_reminders_per_invoice", 2)
		frappe.clear_document_cache(SETTINGS, SETTINGS)
		self.notifications.reset_mock()
		send_fee_reminders()
		self.assertFalse(self.reminders_for())

		fresh = make_invoice(self.student, 300, due_date=add_days(nowdate(), 2))
		self.notifications.reset_mock()
		send_fee_reminders()
		self.assertEqual(self.reminded_invoices(), {fresh.name}, "a capped invoice is left out of the message")

		frappe.db.set_single_value(SETTINGS, {"max_reminders_per_invoice": 0, "send_fee_reminders": 0})
		frappe.clear_document_cache(SETTINGS, SETTINGS)
		self.notifications.reset_mock()
		self.assertEqual(send_fee_reminders(), 0)
		self.assertFalse(self.notified("fee_reminder"))

	def test_reminder_cap_has_a_finite_default(self):
		from hostel360.hostel_fees.setup import SETTINGS_DEFAULTS

		self.assertGreater(SETTINGS_DEFAULTS["max_reminders_per_invoice"], 0)
		self.assertGreater(cint(frappe.get_meta(SETTINGS).get_field("max_reminders_per_invoice").default), 0)

	# -- hostel of a student who has vacated -------------------------------------------------

	def test_vacated_student_keeps_last_hostel_on_new_invoices(self):
		if not frappe.get_meta("Hostel Student").has_field("last_hostel"):
			self.skipTest("Hostel Student.last_hostel is not installed on this site")
		frappe.db.set_value(
			"Hostel Student",
			self.student,
			{"current_hostel": None, "last_hostel": self.hostel, "status": "Vacated"},
			update_modified=False,
		)
		invoice = make_invoice(self.student, 400)
		self.assertEqual(invoice.hostel, self.hostel)
		receipt = make_payment(self.student, [(invoice.name, 400)])
		self.assertEqual(receipt.hostel, self.hostel)

	# -- hostel the invoice is filed under ---------------------------------------------------

	def draft_invoice(self, student: str, **values):
		return frappe.get_doc(
			{
				"doctype": INVOICE,
				"student": student,
				"invoice_type": "Fine",
				"due_date": add_days(nowdate(), 7),
				"items": [{"fee_head": "Fine", "amount": 250}],
				**values,
			}
		)

	def test_hostel_follows_the_student_not_the_request(self):
		elsewhere = make_hostel()
		invoice = self.draft_invoice(self.student, hostel=elsewhere).insert(ignore_permissions=True)
		self.assertEqual(invoice.hostel, self.hostel)

		# A draft saved after the student moved is filed under the new hostel.
		move_student(self.student, elsewhere)
		invoice.hostel = self.hostel
		invoice.save(ignore_permissions=True)
		self.assertEqual(invoice.hostel, elsewhere)

		# Without a bed the record carries no hostel, whatever was sent.
		homeless = make_student()
		self.assertIsNone(self.draft_invoice(homeless, hostel=elsewhere).insert(ignore_permissions=True).hostel)

	def test_trusted_callers_and_migration_may_file_elsewhere(self):
		billed_in = make_hostel()
		name = create_invoice(
			self.student, [{"fee_head": "Fine", "amount": 80}], "Fine", add_days(nowdate(), 7), hostel=billed_in
		)
		self.assertEqual(frappe.db.get_value(INVOICE, name, "hostel"), billed_in)

		frappe.flags.hostel360_migration = True
		try:
			historic = self.draft_invoice(self.student, hostel=billed_in).insert()
		finally:
			frappe.flags.hostel360_migration = False
		self.assertEqual(historic.hostel, billed_in)

	def test_scoped_staff_cannot_file_invoices_for_other_hostels(self):
		other_hostel = make_hostel()
		outsider = make_student(hostel=other_hostel)
		clerk = make_scoped_user(self.hostel)

		with set_user(clerk):
			# Sending their own hostel used to pass Frappe's check made before validate.
			with self.assertRaises(frappe.PermissionError):
				self.draft_invoice(outsider, hostel=self.hostel).insert()

			own = self.draft_invoice(self.student).insert()
			self.assertEqual(own.hostel, self.hostel)
			own.submit()
			self.assertEqual(own.docstatus, 1)

		self.assertFalse(frappe.db.exists(INVOICE, {"student": outsider}))

	def test_scoped_staff_cannot_submit_a_draft_whose_student_moved_away(self):
		clerk = make_scoped_user(self.hostel)
		with set_user(clerk):
			draft = self.draft_invoice(self.student).insert()

		move_student(self.student, make_hostel())
		with set_user(clerk):
			draft = frappe.get_doc(INVOICE, draft.name)
			with self.assertRaises(frappe.PermissionError):
				draft.submit()
