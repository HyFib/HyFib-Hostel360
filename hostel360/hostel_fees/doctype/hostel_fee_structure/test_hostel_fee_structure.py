# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.utils import add_days, nowdate

from hostel360.hostel_fees.constants import INVOICE, STRUCTURE
from hostel360.hostel_fees.doctype.hostel_fee_structure.hostel_fee_structure import run_publish_dues
from hostel360.hostel_fees.services import cancel_invoice, get_applicable_fee_structure
from hostel360.hostel_fees.tests import fixtures as fee_fixtures
from hostel360.hostel_fees.tests.fixtures import (
	FeeTestCase,
	make_academic_year,
	make_hostel,
	make_program,
	make_student,
	unique,
)

# Read by the Frappe test runner: fee tests build every linked record they need themselves.
IGNORE_TEST_RECORD_DEPENDENCIES = fee_fixtures.IGNORE_TEST_RECORD_DEPENDENCIES


def make_structure(hostel=None, year_of_study=None, program=None, items=None, **values):
	doc = frappe.get_doc(
		{
			"doctype": STRUCTURE,
			"structure_name": unique("Hostel Fee "),
			"academic_year": make_academic_year(),
			"hostel": hostel,
			"year_of_study": year_of_study,
			"program": program,
			"due_date": add_days(nowdate(), 20),
			"is_active": 1,
			"items": items
			or [
				{"fee_head": "Hostel Rent", "amount": 24000, "description": "Room rent for the year"},
				{"fee_head": "Caution Deposit", "amount": 5000},
			],
			**values,
		}
	)
	return doc.insert()


class TestHostelFeeStructure(FeeTestCase):
	def setUp(self):
		super().setUp()
		self.hostel = make_hostel()

	def test_total_and_item_rules(self):
		structure = make_structure(self.hostel)
		self.assertEqual(structure.total_amount, 29000)

		with self.assertRaises(frappe.ValidationError):
			make_structure(
				self.hostel,
				items=[
					{"fee_head": "Hostel Rent", "amount": 100},
					{"fee_head": "Hostel Rent", "amount": 200},
				],
			)
		with self.assertRaises(frappe.ValidationError):
			make_structure(self.hostel, items=[{"fee_head": "Hostel Rent", "amount": 0}])

	def test_publish_dues_targets_active_validated_matching_students(self):
		match = make_student(hostel=self.hostel, year_of_study="1")
		other_year = make_student(hostel=self.hostel, year_of_study="2")
		pending = make_student(hostel=self.hostel, year_of_study="1", validation_status="Pending")
		vacated = make_student(hostel=self.hostel, year_of_study="1", status="Vacated")
		other_hostel = make_student(hostel=make_hostel(), year_of_study="1")

		structure = make_structure(self.hostel, year_of_study="1")
		result = run_publish_dues(structure.name, user="Administrator")
		self.assertEqual((result["created"], result["skipped"], result["failed"]), (1, 0, []))

		invoiced = set(frappe.get_all(INVOICE, filters={"fee_structure": structure.name}, pluck="student"))
		self.assertEqual(invoiced, {match})
		for student in (other_year, pending, vacated, other_hostel):
			self.assertNotIn(student, invoiced)

		invoice = frappe.get_doc(INVOICE, {"fee_structure": structure.name})
		self.assertEqual(invoice.docstatus, 1)
		self.assertEqual(invoice.total_amount, 29000)
		self.assertEqual(invoice.invoice_type, "Hostel Fee")
		self.assertEqual(str(invoice.due_date), str(structure.due_date))
		self.assertEqual((invoice.reference_doctype, invoice.reference_name), (STRUCTURE, structure.name))
		self.assertEqual([row.fee_head for row in invoice.items], ["Hostel Rent", "Caution Deposit"])

		sent = self.notified("fee_due_published", invoice.name)
		self.assertEqual(len(sent), 1)
		self.assertTrue(sent[0]["include_guardians"])

		structure.reload()
		self.assertEqual((structure.publish_status, structure.invoices_created), ("Completed", 1))

	def test_publish_dues_is_idempotent(self):
		students = [make_student(hostel=self.hostel) for _ in range(3)]
		structure = make_structure(self.hostel)

		self.assertEqual(run_publish_dues(structure.name)["created"], 3)
		again = run_publish_dues(structure.name)
		self.assertEqual((again["created"], again["skipped"]), (0, 3))
		for student in students:
			self.assertEqual(
				frappe.db.count(INVOICE, {"fee_structure": structure.name, "student": student}), 1
			)

		# A student admitted after publishing is picked up by the next run.
		late = make_student(hostel=self.hostel)
		self.assertEqual(run_publish_dues(structure.name)["created"], 1)
		self.assertTrue(frappe.db.exists(INVOICE, {"fee_structure": structure.name, "student": late}))

	def test_cancelled_invoices_are_reissued_only_on_request(self):
		student = make_student(hostel=self.hostel)
		structure = make_structure(self.hostel)
		run_publish_dues(structure.name)
		cancel_invoice(frappe.db.get_value(INVOICE, {"fee_structure": structure.name, "student": student}))

		preview = structure.get_publish_preview()
		self.assertEqual((preview["to_invoice"], preview["cancelled_only"]), (0, 1))
		self.assertEqual(run_publish_dues(structure.name)["created"], 0)

		preview = structure.get_publish_preview(reissue_cancelled=1)
		self.assertEqual(preview["to_invoice"], 1)
		self.assertEqual(run_publish_dues(structure.name, reissue_cancelled=1)["created"], 1)
		self.assertEqual(
			frappe.db.count(INVOICE, {"fee_structure": structure.name, "student": student, "docstatus": 1}), 1
		)

	def test_one_failure_does_not_stop_publishing(self):
		good = make_student(hostel=self.hostel)
		bad = make_student(hostel=self.hostel)
		structure = make_structure(self.hostel)

		from hostel360.hostel_fees import services

		original = services.create_invoice

		def flaky(student, *args, **kwargs):
			if student == bad:
				raise frappe.ValidationError("simulated failure")
			return original(student, *args, **kwargs)

		with patch.object(services, "create_invoice", side_effect=flaky):
			result = run_publish_dues(structure.name)

		self.assertEqual((result["created"], result["failed"]), (1, [bad]))
		self.assertTrue(frappe.db.exists(INVOICE, {"fee_structure": structure.name, "student": good}))
		self.assertEqual(
			frappe.db.get_value(STRUCTURE, structure.name, "publish_status"), "Completed with Errors"
		)

	def test_publish_dues_queues_a_long_job(self):
		make_student(hostel=self.hostel)
		structure = make_structure(self.hostel)
		with patch("frappe.enqueue") as enqueue:
			self.assertEqual(structure.publish_dues(), {"queued": True})

		kwargs = enqueue.call_args.kwargs
		self.assertEqual(kwargs["queue"], "long")
		self.assertEqual(kwargs["structure"], structure.name)
		self.assertTrue(kwargs["deduplicate"])
		self.assertTrue(kwargs["enqueue_after_commit"])
		self.assertIn(structure.name, kwargs["job_id"])
		self.assertEqual(frappe.db.get_value(STRUCTURE, structure.name, "publish_status"), "Queued")

		structure.reload()
		structure.is_active = 0
		structure.save()
		with self.assertRaises(frappe.ValidationError):
			structure.publish_dues()

	def test_most_specific_structure_applies(self):
		program = make_program()
		student = make_student(hostel=self.hostel, year_of_study="2", program=program)

		general = make_structure()
		by_hostel = make_structure(self.hostel)
		by_hostel_and_year = make_structure(self.hostel, year_of_study="2")
		make_structure(self.hostel, year_of_study="3")
		make_structure(make_hostel())

		self.assertEqual(get_applicable_fee_structure(student), by_hostel_and_year.name)
		frappe.db.set_value(STRUCTURE, by_hostel_and_year.name, "is_active", 0)
		self.assertEqual(get_applicable_fee_structure(student), by_hostel.name)
		frappe.db.set_value(STRUCTURE, by_hostel.name, "is_active", 0)
		self.assertEqual(get_applicable_fee_structure(student), general.name)

	def test_hostel_scoped_staff_only_bill_their_hostels(self):
		from frappe.permissions import add_user_permission

		from hostel360.setup.roles import OFFICE_ROLE

		other_hostel = make_hostel()
		mine = make_student(hostel=self.hostel)
		theirs = make_student(hostel=other_hostel)

		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"{unique('office.').lower()}@example.com",
				"first_name": "Office",
				"send_welcome_email": 0,
				"roles": [{"role": OFFICE_ROLE}],
			}
		).insert(ignore_permissions=True)
		add_user_permission("Hostel", self.hostel, user.name, ignore_permissions=True)

		general = make_structure()
		self.assertEqual(run_publish_dues(general.name, user=user.name)["created"], 1)
		invoiced = set(frappe.get_all(INVOICE, filters={"fee_structure": general.name}, pluck="student"))
		self.assertIn(mine, invoiced)
		self.assertNotIn(theirs, invoiced)

		elsewhere = make_structure(other_hostel)
		self.assertEqual(run_publish_dues(elsewhere.name, user=user.name)["created"], 0)
