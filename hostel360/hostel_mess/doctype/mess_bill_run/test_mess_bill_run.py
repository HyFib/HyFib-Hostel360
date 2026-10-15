# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase, set_user
from frappe.utils import add_days, flt, getdate, nowdate

from hostel360.hostel_mess import billing
from hostel360.hostel_mess.billing import StudentBill, build_bill_run, divide_cost, get_enrolled_days
from hostel360.hostel_mess.services import apply_mess_reduction, list_mess_bills
from hostel360.hostel_mess.testing import (
	EXTERNAL_DEPENDENCIES,
	make_allotment,
	make_expense,
	make_hostel,
	make_item,
	make_mess,
	make_reduction,
	make_stock_entry,
	make_student,
	make_user,
	purge_committed_records,
	set_mess_settings,
)
from hostel360.hostel_mess.utils import BooksChangedError
from hostel360.setup.roles import DEPUTY_WARDEN_ROLE, MESS_MANAGER_ROLE

IGNORE_TEST_RECORD_DEPENDENCIES = EXTERNAL_DEPENDENCIES

PERIOD = ("2026-09-01", "2026-09-30")
RUN_MODULE = "hostel360.hostel_mess.doctype.mess_bill_run.mess_bill_run"


class TestMessBillRun(IntegrationTestCase):
	"""September 2026 at a mess serving two hostels.

	Cost: provisions issued 1,00,000 + expenses 25,000 - credits 5,000 = 1,20,000.
	Chargeable days: A 30, B 25, C 20, D 10, G 30 = 115, so the daily rate is
	1,20,000 / 115 = 1,043.478... -> 1,043.48.
	"""

	def setUp(self):
		set_mess_settings(bill_rounding="Nearest Rupee", bill_due_days=10)
		self.h1, self.h2, self.h3 = make_hostel(), make_hostel(), make_hostel()
		self.mess = make_mess([self.h1, self.h2])
		s = {}

		# A: resident for the whole month.
		s["A"] = make_student(self.h1)
		make_allotment(s["A"], self.h1, "2026-08-01")

		# B: whole month; reduction from 28 Aug clipped to 1-5 Sep (5 days); extras 150.
		s["B"] = make_student(self.h1)
		make_allotment(s["B"], self.h1, "2026-06-01")
		make_reduction(s["B"], "2026-08-28", "2026-09-05")

		# C: joined on 11 Sep (20 days).
		s["C"] = make_student(self.h2)
		make_allotment(s["C"], self.h2, "2026-09-11")

		# D: vacated on 15 Sep (15 days) with reductions 5-7 Sep and 14-18 Sep (2 of them enrolled).
		s["D"] = make_student(None)
		make_allotment(s["D"], self.h1, "2026-07-01", vacated_on="2026-09-15", status="Vacated")
		make_reduction(s["D"], "2026-09-05", "2026-09-07")
		make_reduction(s["D"], "2026-09-14", "2026-09-18")

		# G: transferred between two served hostels on 10 Sep without an end date on the old
		# allotment; the day of transfer must not be counted twice (30 days).
		s["G"] = make_student(self.h2)
		old = make_allotment(s["G"], self.h1, "2026-08-01", status="Transferred")
		make_allotment(s["G"], self.h2, "2026-09-10", previous_allotment=old)

		# Not billed for days: not enrolled in the mess, unserved hostel, cancelled or draft
		# allotments, allotments outside the period, a rejected reduction is ignored.
		s["E"] = make_student(self.h1, mess_enrolled=0)
		make_allotment(s["E"], self.h1, "2026-08-01")
		s["F"] = make_student(self.h3)
		make_allotment(s["F"], self.h3, "2026-08-01")
		s["X"] = make_student(self.h1)
		make_allotment(s["X"], self.h1, "2026-08-01", status="Cancelled")
		s["Y"] = make_student(self.h1)
		make_allotment(s["Y"], self.h1, "2026-08-01", docstatus=0)
		s["W"] = make_student(self.h1)
		make_allotment(s["W"], self.h1, "2026-06-01", to_date="2026-08-31")
		make_reduction(s["A"], "2026-09-20", "2026-09-25", status="Rejected")

		# K: no stay in the period, only an extra charge.
		s["K"] = make_student(self.h1)
		self.students = s

		self.make_extra(s["B"], 100, "2026-09-12")
		self.make_extra(s["B"], 50, "2026-09-20")
		self.make_extra(s["K"], 75, "2026-09-03")
		self.october_extra = self.make_extra(s["A"], 40, "2026-10-02")

		rice = make_item("Kg")
		make_stock_entry(self.mess, "Receipt", [(rice, 2010, 50)], "2026-08-30")
		make_stock_entry(self.mess, "Issue", [(rice, 10)], "2026-08-31")
		make_stock_entry(self.mess, "Issue", [(rice, 2000)], "2026-09-20")
		make_expense(self.mess, 25000, "2026-09-25")
		make_expense(self.mess, 999, "2026-09-26", submit=False)
		make_expense(self.mess, 4000, "2026-10-01")

	def make_extra(self, student, amount, date):
		return (
			frappe.get_doc(
				{
					"doctype": "Mess Extra Charge",
					"student": student,
					"mess": self.mess,
					"charge_date": date,
					"charge_type": "Guest Meal",
					"description": "Guest meal",
					"amount": amount,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def make_run(self, other_credits=5000):
		return frappe.get_doc(
			{
				"doctype": "Mess Bill Run",
				"mess": self.mess,
				"from_date": PERIOD[0],
				"to_date": PERIOD[1],
				"other_credits": other_credits,
			}
		).insert(ignore_permissions=True)

	def bills(self, run):
		return {
			row.student: row
			for row in frappe.get_all(
				"Mess Bill",
				filters={"mess_bill_run": run.name},
				fields=[
					"name",
					"student",
					"status",
					"enrolled_days",
					"reduction_days",
					"chargeable_days",
					"daily_rate",
					"mess_charge",
					"extras_amount",
					"rounding_adjustment",
					"total_amount",
					"fee_invoice",
					"hostel",
				],
			)
		}

	# ------------------------------------------------------------------ computation

	def test_dividing_system_with_known_numbers(self):
		run = self.make_run()
		run.compute_bills()

		self.assertEqual(run.status, "Computed")
		self.assertEqual(flt(run.provision_cost), 100000)
		self.assertEqual(flt(run.other_expenses), 25000)
		self.assertEqual(flt(run.total_cost), 120000)
		self.assertEqual(run.total_student_days, 115)
		self.assertEqual(flt(run.daily_rate), 1043.48)
		self.assertEqual(run.bills_count, 6)
		self.assertEqual(flt(run.total_extras), 225)
		self.assertEqual(flt(run.rate_rounding_difference), 0.20)
		self.assertEqual(flt(run.rounding_adjustment), -0.20)
		self.assertEqual(flt(run.total_billed), 120225)

		s = self.students
		expected = {
			# student: (enrolled, reduction, chargeable, mess charge, extras, rounding, total)
			s["A"]: (30, 0, 30, 31304.40, 0, -0.40, 31304),
			s["B"]: (30, 5, 25, 26087.00, 150, 0, 26237),
			s["C"]: (20, 0, 20, 20869.60, 0, 0.40, 20870),
			s["D"]: (15, 5, 10, 10434.80, 0, 0.20, 10435),
			s["G"]: (30, 0, 30, 31304.40, 0, -0.40, 31304),
			s["K"]: (0, 0, 0, 0, 75, 0, 75),
		}
		bills = self.bills(run)
		self.assertEqual(set(bills), set(expected))
		for student, values in expected.items():
			bill = bills[student]
			actual = (
				bill.enrolled_days,
				bill.reduction_days,
				bill.chargeable_days,
				flt(bill.mess_charge, 2),
				flt(bill.extras_amount, 2),
				flt(bill.rounding_adjustment, 2),
				flt(bill.total_amount, 2),
			)
			self.assertEqual(actual, values, msg=f"bill of {student}")
			self.assertEqual(flt(bill.daily_rate, 2), 1043.48)
			self.assertEqual(bill.status, "Draft")
		self.assertEqual(bills[s["C"]].hostel, self.h2)
		self.assertEqual(bills[s["D"]].hostel, self.h1)

	def test_compute_is_idempotent_and_tracks_changes(self):
		run = self.make_run()
		run.compute_bills()
		first = self.bills(run)
		run.compute_bills()
		second = self.bills(run)
		self.assertEqual(set(first), set(second))
		self.assertEqual(frappe.db.count("Mess Bill", {"mess_bill_run": run.name}), 6)

		run.reload()
		run.other_credits = 5000 + 1150  # total cost 1,18,850 -> 1,033.48 per day
		run.save()
		self.assertEqual(run.status, "Draft")
		run.compute_bills()
		self.assertEqual(flt(run.daily_rate), 1033.48)

	def test_rounding_can_be_disabled(self):
		set_mess_settings(bill_rounding="None")
		run = self.make_run()
		run.compute_bills()
		bill = self.bills(run)[self.students["A"]]
		self.assertEqual(flt(bill.total_amount, 2), 31304.40)
		self.assertEqual(flt(bill.rounding_adjustment), 0)
		self.assertEqual(flt(run.total_billed), 120225.20)

	def test_nothing_to_divide_is_explained(self):
		empty_mess = make_mess([make_hostel()])
		item = make_item("Kg")
		make_stock_entry(empty_mess, "Receipt", [(item, 10, 10)], "2026-09-01")
		make_stock_entry(empty_mess, "Issue", [(item, 10)], "2026-09-02")
		run = frappe.get_doc(
			{"doctype": "Mess Bill Run", "mess": empty_mess, "from_date": PERIOD[0], "to_date": PERIOD[1]}
		).insert(ignore_permissions=True)
		with self.assertRaisesRegex(frappe.ValidationError, "no chargeable student-days"):
			run.compute_bills()

		no_cost_mess = make_mess([make_hostel()])
		run = frappe.get_doc(
			{"doctype": "Mess Bill Run", "mess": no_cost_mess, "from_date": PERIOD[0], "to_date": PERIOD[1]}
		).insert(ignore_permissions=True)
		with self.assertRaisesRegex(frappe.ValidationError, "No mess cost"):
			run.compute_bills()

	def test_divide_cost_arithmetic(self):
		bills = [StudentBill("S1", enrolled_days=31), StudentBill("S2", enrolled_days=31, reduction_days=31)]
		days, rate = divide_cost(1000, bills, "Nearest Rupee")
		self.assertEqual((days, rate), (31, 32.26))
		self.assertEqual(bills[0].mess_charge, 1000.06)
		self.assertEqual(bills[0].total_amount, 1000)
		self.assertEqual(bills[1].total_amount, 0)

	def test_overlapping_and_future_runs_are_refused(self):
		self.make_run()
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Mess Bill Run",
					"mess": self.mess,
					"from_date": "2026-09-20",
					"to_date": "2026-09-25",
				}
			).insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Mess Bill Run",
					"mess": self.mess,
					"from_date": nowdate(),
					"to_date": add_days(nowdate(), 5),
				}
			).insert(ignore_permissions=True)

	# ------------------------------------------------------------------ publish

	def test_publish_creates_one_invoice_per_bill(self):
		run = self.make_run()
		run.compute_bills()
		create_invoice = MagicMock(side_effect=lambda **kw: f"TEST-INV-{kw['reference_name']}")

		with (
			patch("hostel360.hostel_mess.billing.get_fees_service", return_value=create_invoice),
			patch(f"{RUN_MODULE}.send_notification") as notify,
		):
			run.submit()

		self.assertEqual(run.status, "Published")
		self.assertEqual(getdate(run.due_date), getdate(add_days(nowdate(), 10)))
		self.assertEqual(create_invoice.call_count, 6)
		self.assertEqual(notify.call_count, 6)
		self.assertEqual(notify.call_args.args[0], "mess_bill_published")

		bills = self.bills(run)
		for call in create_invoice.call_args_list:
			kwargs = call.kwargs
			bill = next(b for b in bills.values() if b.name == kwargs["reference_name"])
			self.assertEqual(kwargs["invoice_type"], "Mess Bill")
			self.assertEqual(kwargs["reference_doctype"], "Mess Bill")
			self.assertEqual(kwargs["student"], bill.student)
			self.assertEqual(flt(sum(i["amount"] for i in kwargs["items"]), 2), flt(bill.total_amount, 2))
			self.assertTrue({i["fee_head"] for i in kwargs["items"]} <= {"Mess Charges", "Mess Extras"})

		b_items = next(
			c.kwargs["items"]
			for c in create_invoice.call_args_list
			if c.kwargs["student"] == self.students["B"]
		)
		self.assertEqual(
			[(i["fee_head"], i["amount"]) for i in b_items], [("Mess Charges", 26087), ("Mess Extras", 150)]
		)
		a_items = next(
			c.kwargs["items"]
			for c in create_invoice.call_args_list
			if c.kwargs["student"] == self.students["A"]
		)
		self.assertEqual([(i["fee_head"], i["amount"]) for i in a_items], [("Mess Charges", 31304)])

		for bill in bills.values():
			self.assertEqual(bill.status, "Published")
			self.assertEqual(bill.fee_invoice, f"TEST-INV-{bill.name}")

		billed = frappe.get_all(
			"Mess Extra Charge", filters={"student": self.students["B"]}, fields=["status", "mess_bill"]
		)
		self.assertTrue(all(row.status == "Billed" for row in billed))
		self.assertEqual({row.mess_bill for row in billed}, {bills[self.students["B"]].name})
		self.assertEqual(frappe.db.get_value("Mess Extra Charge", self.october_extra, "status"), "Unbilled")

		portal_bills = list_mess_bills(self.students["B"])
		self.assertEqual(len(portal_bills), 1)
		self.assertEqual(portal_bills[0]["total_amount"], 26237)

	def test_publish_refuses_stale_figures(self):
		run = self.make_run()
		run.compute_bills()
		make_expense(self.mess, 500, "2026-09-28")
		with self.assertRaisesRegex(frappe.ValidationError, "Compute Bills again"):
			run.submit()

	def test_published_period_is_closed(self):
		run = self.make_run()
		run.compute_bills()
		with (
			patch(
				"hostel360.hostel_mess.billing.get_fees_service",
				return_value=MagicMock(return_value="TEST-INV"),
			),
			patch(f"{RUN_MODULE}.send_notification"),
		):
			run.submit()

		item = make_item("Kg")
		with self.assertRaisesRegex(frappe.ValidationError, "published up to"):
			make_stock_entry(self.mess, "Receipt", [(item, 1, 10)], "2026-09-29")
		with self.assertRaisesRegex(frappe.ValidationError, "published up to"):
			make_expense(self.mess, 100, "2026-09-29")
		with self.assertRaisesRegex(frappe.ValidationError, "published up to"):
			self.make_extra(self.students["A"], 10, "2026-09-29")
		with self.assertRaisesRegex(frappe.ValidationError, "published up to"):
			apply_mess_reduction(
				self.students["A"], "2026-09-26", "2026-09-30", "Medical", user="Administrator"
			)

	def test_publish_and_cancel_with_fee_invoices(self):
		if not frappe.db.exists("Hostel Fee Head", "Mess Charges"):
			self.skipTest("Hostel Fees defaults are not installed on this site")
		run = self.make_run()
		run.compute_bills()
		with patch(f"{RUN_MODULE}.send_notification"):
			run.submit()

		bills = self.bills(run)
		invoices = {student: bill.fee_invoice for student, bill in bills.items()}
		self.assertTrue(all(invoices.values()))
		for bill in bills.values():
			invoice = frappe.get_doc("Hostel Fee Invoice", bill.fee_invoice)
			self.assertEqual(invoice.docstatus, 1)
			self.assertEqual(invoice.invoice_type, "Mess Bill")
			self.assertEqual(invoice.reference_name, bill.name)
			self.assertEqual(flt(invoice.total_amount, 2), flt(bill.total_amount, 2))

		self.assertEqual(list_mess_bills(self.students["A"])[0]["invoice_status"], "Unpaid")

		# A payment against any invoice blocks cancellation.
		paid_invoice = invoices[self.students["C"]]
		frappe.db.set_value("Hostel Fee Invoice", paid_invoice, {"paid_amount": 100, "status": "Partly Paid"})
		run.reload()
		with self.assertRaisesRegex(frappe.ValidationError, "Payments have been received"):
			run.cancel()

		frappe.db.set_value("Hostel Fee Invoice", paid_invoice, {"paid_amount": 0, "status": "Unpaid"})
		run.reload()
		run.cancel()
		self.assertEqual(run.status, "Cancelled")
		for invoice in invoices.values():
			self.assertEqual(frappe.db.get_value("Hostel Fee Invoice", invoice, "docstatus"), 2)
		self.assertEqual({b.status for b in self.bills(run).values()}, {"Cancelled"})
		self.assertEqual(
			set(frappe.get_all("Mess Extra Charge", filters={"student": self.students["B"]}, pluck="status")),
			{"Unbilled"},
		)

	def test_cancel_guard_uses_invoice_payment_state(self):
		run = self.make_run()
		run.compute_bills()
		with (
			patch(
				"hostel360.hostel_mess.billing.get_fees_service",
				return_value=MagicMock(return_value="TEST-INV"),
			),
			patch(f"{RUN_MODULE}.send_notification"),
		):
			run.submit()
		with patch(
			"hostel360.hostel_mess.billing.get_invoice_payment_state",
			return_value=[frappe._dict(name="TEST-INV", status="Paid")],
		):
			with self.assertRaises(frappe.ValidationError):
				run.cancel()

	def test_print_formats_render(self):
		run = self.make_run()
		run.compute_bills()
		bill = self.bills(run)[self.students["B"]]
		html = frappe.get_print("Mess Bill", bill.name, print_format="Mess Bill")
		self.assertIn("1,043.48", html)
		self.assertIn("26,237.00", html)
		summary = frappe.get_print("Mess Bill Run", run.name, print_format="Mess Bill Run Summary")
		self.assertIn("1,20,000.00", summary)
		self.assertNotIn("no such element", summary)
		self.assertNotIn("no such element", html)

	# ------------------------------------------------------------------ endpoints and jobs

	def test_compute_endpoint(self):
		from hostel360.hostel_mess.doctype.mess_bill_run import mess_bill_run as module

		run = self.make_run()
		result = module.compute(run.name)
		self.assertEqual(result, {"queued": False, "bills_count": 6, "daily_rate": 1043.48})

		with patch.object(module, "BACKGROUND_THRESHOLD", 0), patch("frappe.enqueue") as enqueue:
			self.assertEqual(module.compute(run.name), {"queued": True})
		kwargs = enqueue.call_args.kwargs
		self.assertEqual(kwargs["queue"], "long")
		self.assertEqual(kwargs["job_id"], f"mess-bill-run-compute::{run.name}")
		self.assertTrue(kwargs["deduplicate"])

	def test_background_compute(self):
		from hostel360.hostel_mess.doctype.mess_bill_run.mess_bill_run import compute_in_background

		run = self.make_run()
		with patch("frappe.publish_realtime") as realtime:
			compute_in_background(run.name, "Administrator")
		self.assertEqual(realtime.call_args.args[0], "mess_bill_run_computed")
		self.assertEqual(frappe.db.get_value("Mess Bill Run", run.name, "status"), "Computed")

		empty = frappe.get_doc(
			{
				"doctype": "Mess Bill Run",
				"mess": make_mess([make_hostel()]),
				"from_date": PERIOD[0],
				"to_date": PERIOD[1],
			}
		).insert(ignore_permissions=True)
		with patch("frappe.publish_realtime") as realtime:
			compute_in_background(empty.name, "Administrator")
		self.assertEqual(realtime.call_args.args[0], "mess_bill_run_compute_failed")
		self.assertIn("No mess cost", realtime.call_args.args[1]["error"])
		# The failed job leaves earlier work in the transaction intact.
		self.assertEqual(frappe.db.get_value("Mess Bill Run", run.name, "status"), "Computed")

	def test_reports(self):
		from hostel360.hostel_mess.report.mess_consumption.mess_consumption import execute as consumption
		from hostel360.hostel_mess.report.mess_cost_summary.mess_cost_summary import execute as cost_summary
		from hostel360.hostel_mess.report.mess_reduction_register.mess_reduction_register import (
			execute as reduction_register,
		)
		from hostel360.hostel_mess.report.mess_stock_balance.mess_stock_balance import (
			execute as stock_balance,
		)

		_columns, data, _message, _chart, _summary = stock_balance(
			{"mess": self.mess, "as_on_date": "2026-08-31"}
		)
		self.assertEqual(len(data), 1)
		self.assertEqual(data[0]["qty"], 2000)
		self.assertEqual(data[0]["stock_value"], 100000)

		for granularity, buckets in (("Daily", 30), ("Monthly", 1), ("Yearly", 1)):
			columns, data, _message, chart, _summary = consumption(
				{"mess": self.mess, "from_date": PERIOD[0], "to_date": PERIOD[1], "granularity": granularity}
			)
			self.assertEqual(len(columns), 7 + 2 * buckets)
			self.assertEqual(data[0]["total_qty"], 2000)
			self.assertEqual(data[0]["total_value"], 100000)
			self.assertEqual(sum(chart["data"]["datasets"][0]["values"]), 100000)

		run = self.make_run()
		run.compute_bills()
		with (
			patch(
				"hostel360.hostel_mess.billing.get_fees_service",
				return_value=MagicMock(return_value="TEST-INV"),
			),
			patch(f"{RUN_MODULE}.send_notification"),
		):
			run.submit()
		_columns, data, _message, chart, _summary = cost_summary(
			{"mess": self.mess, "from_date": "2026-08-01", "to_date": PERIOD[1]}
		)
		august, september = data
		self.assertEqual(august["provision_cost"], 500)
		self.assertIsNone(august["daily_rate"])
		self.assertEqual(september["total_cost"], 120000)
		self.assertEqual(september["student_days"], 115)
		self.assertEqual(september["daily_rate"], 1043.48)
		self.assertEqual(chart["data"]["datasets"][0]["values"], [1043.48])

		_columns, data, _message, _chart, _summary = reduction_register(
			{"mess": None, "from_date": PERIOD[0], "to_date": PERIOD[1], "student": self.students["D"]}
		)
		self.assertEqual(len(data), 2)
		self.assertTrue(all(row.applied_on for row in data))

	def test_deleting_a_draft_run_removes_its_bills(self):
		run = self.make_run()
		run.compute_bills()
		self.assertEqual(frappe.db.count("Mess Bill", {"mess_bill_run": run.name}), 6)
		run.delete(ignore_permissions=True)
		self.assertEqual(frappe.db.count("Mess Bill", {"mess_bill_run": run.name}), 0)

	# ------------------------------------------------------------------ transfers and enrolment

	def test_transfer_between_messes_bills_the_move_day_once(self):
		other_mess = make_mess([self.h3])
		# What Room Allotment.transfer writes: the old stay ends the day before, vacated on the
		# move day, and the new allotment starts on the move day.
		mover = make_student(self.h3)
		old = make_allotment(
			mover, self.h1, "2026-08-01", to_date="2026-09-15", vacated_on="2026-09-16", status="Transferred"
		)
		make_allotment(mover, self.h3, "2026-09-16", previous_allotment=old)
		# Moved on the day the old allotment began: the day belongs to the new hostel only.
		same_day = make_student(self.h3)
		old = make_allotment(
			same_day,
			self.h1,
			"2026-09-16",
			to_date="2026-09-16",
			vacated_on="2026-09-16",
			status="Transferred",
		)
		make_allotment(same_day, self.h3, "2026-09-16", previous_allotment=old)

		first = get_enrolled_days(self.mess, *PERIOD)
		second = get_enrolled_days(other_mess, *PERIOD)
		self.assertEqual(len(first[mover]["days"]), 15)
		self.assertEqual(max(first[mover]["days"]), getdate("2026-09-15").toordinal())
		self.assertEqual(len(second[mover]["days"]), 15)
		self.assertEqual(min(second[mover]["days"]), getdate("2026-09-16").toordinal())
		self.assertNotIn(same_day, first)
		self.assertEqual(len(second[same_day]["days"]), 15)

	def record_enrolment_change(self, student, before, after, when):
		version = frappe.get_doc(
			{
				"doctype": "Version",
				"ref_doctype": "Hostel Student",
				"docname": student,
				"data": json.dumps({"changed": [["mess_enrolled", before, after]]}),
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Version", version.name, "creation", when, update_modified=False)

	def test_enrolment_changes_are_charged_by_date(self):
		hostel = make_hostel()
		mess = make_mess([hostel])
		item = make_item("Kg")
		make_stock_entry(mess, "Receipt", [(item, 100, 30)], "2026-08-30")
		make_stock_entry(mess, "Issue", [(item, 100)], "2026-09-10")
		people = {}
		for key, enrolled in (("left", 0), ("joined", 1), ("steady", 1), ("away", 1), ("later", 0)):
			people[key] = make_student(hostel, mess_enrolled=enrolled)
			make_allotment(people[key], hostel, "2026-08-01")
		self.record_enrolment_change(people["left"], 1, 0, "2026-09-20 11:30:00")
		self.record_enrolment_change(people["joined"], 0, 1, "2026-09-20 09:00:00")
		self.record_enrolment_change(people["away"], 1, 0, "2026-09-10 10:00:00")
		self.record_enrolment_change(people["away"], 0, 1, "2026-09-15 10:00:00")
		self.record_enrolment_change(people["later"], 1, 0, "2026-10-05 10:00:00")

		result = build_bill_run(mess, *PERIOD, rounding="None")
		days = {bill.student: bill.enrolled_days for bill in result.bills}
		self.assertEqual(days[people["left"]], 19)  # 1-19 September
		self.assertEqual(days[people["joined"]], 11)  # 20-30 September
		self.assertEqual(days[people["steady"]], 30)
		self.assertEqual(days[people["away"]], 25)  # out from the 10th, back on the 15th
		self.assertEqual(days[people["later"]], 30)  # opted out only in October
		self.assertEqual(result.total_student_days, 115)
		self.assertEqual(
			set(result.enrolment_changes), {people["left"], people["joined"], people["away"], people["later"]}
		)

		run = frappe.get_doc(
			{"doctype": "Mess Bill Run", "mess": mess, "from_date": PERIOD[0], "to_date": PERIOD[1]}
		).insert(ignore_permissions=True)
		with patch("frappe.msgprint") as msgprint:
			run.compute_bills()
		self.assertTrue(
			any(call.kwargs.get("title") == "Enrolment Changes" for call in msgprint.call_args_list)
		)

	# ------------------------------------------------------------------ invoices amended by the office

	def publish_with_fee_invoices(self):
		if not frappe.db.exists("Hostel Fee Head", "Mess Charges"):
			self.skipTest("Hostel Fees defaults are not installed on this site")
		run = self.make_run()
		run.compute_bills()
		with patch(f"{RUN_MODULE}.send_notification"):
			run.submit()
		return run

	def amend_invoice(self, invoice_name, amount):
		"""What the office does from the desk: cancel the invoice, then amend and submit it."""
		original = frappe.get_doc("Hostel Fee Invoice", invoice_name)
		original.cancel()
		amended = frappe.copy_doc(original)
		amended.amended_from = original.name
		amended.docstatus = 0
		amended.reference_doctype = original.reference_doctype
		amended.reference_name = original.reference_name
		amended.items = amended.items[:1]
		amended.items[0].amount = amount
		amended.insert(ignore_permissions=True)
		amended.submit()
		return amended

	def test_amended_invoice_stands_for_the_bill(self):
		run = self.publish_with_fee_invoices()
		bill = self.bills(run)[self.students["C"]]
		amended = self.amend_invoice(bill.fee_invoice, 15000)

		portal = list_mess_bills(self.students["C"])[0]
		self.assertEqual((portal["fee_invoice"], portal["invoice_status"]), (amended.name, "Unpaid"))
		run.reload()
		run.run_method("onload")
		summary = {row.status: row.count for row in run.get_onload().invoice_summary}
		self.assertEqual(summary, {"Unpaid": 6})
		self.assertEqual(len(run.get_invoices()), 6)
		self.assertEqual(
			{b.student: b.fee_invoice for b in run.get_bills()}[self.students["C"]], amended.name
		)
		self.assertIn(amended.name, run.get_invoices())
		self.assertNotIn(bill.fee_invoice, run.get_invoices())

		billing.sync_amended_invoice(amended)
		self.assertEqual(frappe.db.get_value("Mess Bill", bill.name, "fee_invoice"), amended.name)

	def test_payment_on_an_amended_invoice_blocks_cancel(self):
		run = self.publish_with_fee_invoices()
		bill = self.bills(run)[self.students["C"]]
		amended = self.amend_invoice(bill.fee_invoice, 15000)

		frappe.db.set_value(
			"Hostel Fee Invoice", amended.name, {"paid_amount": 1000, "status": "Partly Paid"}
		)
		run.reload()
		with self.assertRaisesRegex(frappe.ValidationError, "Payments have been received"):
			run.cancel()

		# Unpaid, the amendment is withdrawn together with the run's own invoices.
		frappe.db.set_value("Hostel Fee Invoice", amended.name, {"paid_amount": 0, "status": "Unpaid"})
		run.reload()
		run.cancel()
		self.assertEqual(frappe.db.get_value("Hostel Fee Invoice", amended.name, "docstatus"), 2)

	# ------------------------------------------------------------------ closing the period

	def test_publishing_and_cancelling_maintain_billed_upto(self):
		self.assertIsNone(frappe.db.get_value("Hostel Mess", self.mess, "billed_upto"))
		run = self.make_run()
		run.compute_bills()
		with (
			patch(
				"hostel360.hostel_mess.billing.get_fees_service", return_value=MagicMock(return_value=None)
			),
			patch(f"{RUN_MODULE}.send_notification"),
		):
			run.submit()
			self.assertEqual(
				getdate(frappe.db.get_value("Hostel Mess", self.mess, "billed_upto")), getdate(PERIOD[1])
			)
			run.reload()
			run.cancel()
		self.assertIsNone(frappe.db.get_value("Hostel Mess", self.mess, "billed_upto"))

		from hostel360.hostel_mess.setup import sync_billed_upto

		frappe.db.set_value("Hostel Mess", self.mess, "billed_upto", "2026-01-31")
		sync_billed_upto()
		self.assertIsNone(frappe.db.get_value("Hostel Mess", self.mess, "billed_upto"))

	# ------------------------------------------------------------------ hostel-scoped viewers

	def test_summary_print_shows_a_deputy_only_their_hostels(self):
		run = self.make_run()
		run.compute_bills()
		deputy = make_user([DEPUTY_WARDEN_ROLE])
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": deputy,
				"allow": "Hostel",
				"for_value": self.h1,
				"apply_to_all_doctypes": 1,
			}
		).insert(ignore_permissions=True)
		with set_user(deputy):
			bills = run.get_bills()
			summary = frappe.get_print("Mess Bill Run", run.name, print_format="Mess Bill Run Summary")
		self.assertEqual({b.hostel for b in bills}, {self.h1})
		self.assertIn(self.students["A"], summary)
		self.assertNotIn(self.students["C"], summary)
		self.assertNotIn(self.students["G"], summary)
		self.assertIn(f"Showing {len(bills)} of 6 bills", summary)

		with set_user(make_user([MESS_MANAGER_ROLE])):
			self.assertEqual(len(run.get_bills()), 6)

	# ------------------------------------------------------------------ large runs in the background

	def test_large_runs_are_queued_on_the_long_queue(self):
		from hostel360.hostel_mess.doctype.mess_bill_run import mess_bill_run as module

		run = self.make_run()
		run.compute_bills()
		with (
			patch.object(module, "BACKGROUND_THRESHOLD", 0),
			patch.object(module, "is_scheduler_inactive", return_value=False),
			patch("frappe.enqueue") as enqueue,
		):
			run.run_method("onload")
			self.assertTrue(run.get_onload().runs_in_background)
			self.assertEqual(run.submit(), {"queued": True})
			self.assertEqual(run.docstatus, 0)
			self.assertTrue(run.is_locked)
			with self.assertRaises(frappe.DocumentLockedError):
				module.start_action(run.name, "submit")
		run.unlock()
		kwargs = enqueue.call_args.kwargs
		self.assertEqual(enqueue.call_args.args[0], f"{RUN_MODULE}.run_publishing_action_in_background")
		self.assertEqual(
			(kwargs["queue"], kwargs["job_id"], kwargs["action"], kwargs["mess"]),
			("long", f"mess-bill-run::{run.name}", "submit", self.mess),
		)
		self.assertEqual(frappe.db.get_value("Mess Bill Run", run.name, "docstatus"), 0)

	def test_start_action_runs_small_runs_at_once(self):
		from hostel360.hostel_mess.doctype.mess_bill_run.mess_bill_run import start_action

		run = self.make_run()
		run.compute_bills()
		with (
			patch(
				"hostel360.hostel_mess.billing.get_fees_service", return_value=MagicMock(return_value=None)
			),
			patch(f"{RUN_MODULE}.send_notification"),
		):
			self.assertEqual(start_action(run.name, "submit"), {"queued": False, "status": "Published"})
			self.assertEqual(start_action(run.name, "cancel"), {"queued": False, "status": "Cancelled"})


class TestBillRunConcurrency(IntegrationTestCase):
	"""Publishing while the books change, and publishing in a background worker.

	Each test uses two database connections (two users at the same moment), so the fixtures
	are committed and purged afterwards.
	"""

	def setUp(self):
		set_mess_settings(bill_rounding="Nearest Rupee", bill_due_days=10)
		self.hostel = make_hostel()
		self.mess = make_mess([self.hostel])
		self.item = make_item("Kg")
		self.students = [make_student(self.hostel) for _ in range(3)]
		for student in self.students:
			make_allotment(student, self.hostel, "2026-08-01")
		make_stock_entry(self.mess, "Receipt", [(self.item, 90, 100)], "2026-08-30")
		make_stock_entry(self.mess, "Issue", [(self.item, 90)], "2026-09-10")
		self.run = frappe.get_doc(
			{"doctype": "Mess Bill Run", "mess": self.mess, "from_date": PERIOD[0], "to_date": PERIOD[1]}
		).insert(ignore_permissions=True)
		self.run.compute_bills()
		self.late_expense = make_expense(self.mess, 900, "2026-09-28", submit=False).name
		frappe.db.commit()
		self.addCleanup(
			purge_committed_records,
			messes=[self.mess],
			items=[self.item],
			hostels=[self.hostel],
			students=self.students,
		)

	def publishing(self):
		"""Fee invoices and notifications are not under test here."""
		return (
			patch(
				"hostel360.hostel_mess.billing.get_fees_service", return_value=MagicMock(return_value=None)
			),
			patch(f"{RUN_MODULE}.send_notification"),
		)

	def test_publish_sees_an_entry_committed_just_before(self):
		with self.secondary_connection():
			run = frappe.get_doc("Mess Bill Run", self.run.name)  # the publishing request has started
		with self.primary_connection():
			frappe.get_doc("Mess Expense", self.late_expense).submit()
			frappe.db.commit()
		fees, notify = self.publishing()
		with self.secondary_connection(), fees, notify:
			try:
				with self.assertRaisesRegex(frappe.ValidationError, "Compute Bills again"):
					run.submit()
			finally:
				frappe.db.rollback()
		self.assertEqual(frappe.db.get_value("Mess Bill Run", self.run.name, "docstatus"), 0)

	def race_expense_against_publish(self, snapshot_isolation=True):
		with self.secondary_connection():
			if not snapshot_isolation:
				frappe.db.sql("set session innodb_snapshot_isolation = OFF")
			expense = frappe.get_doc("Mess Expense", self.late_expense)  # the posting request has started
		fees, notify = self.publishing()
		with self.primary_connection(), fees, notify:
			frappe.get_doc("Mess Bill Run", self.run.name).submit()
			frappe.db.commit()
		with self.secondary_connection():
			try:
				with self.assertRaises(frappe.ValidationError) as caught:
					expense.submit()
			finally:
				frappe.db.rollback()
		self.assertEqual(frappe.db.get_value("Mess Expense", self.late_expense, "docstatus"), 0)
		return caught.exception

	def test_entry_racing_a_publish_is_refused(self):
		error = self.race_expense_against_publish()
		self.assertIsInstance(error, BooksChangedError)

	def test_entry_racing_a_publish_is_refused_without_snapshot_isolation(self):
		if not frappe.db.sql("show variables like 'innodb_snapshot_isolation'"):
			self.skipTest("The database server has no innodb_snapshot_isolation setting")
		error = self.race_expense_against_publish(snapshot_isolation=False)
		self.assertIn("published up to", str(error))

	def run_worker(self, action):
		from hostel360.hostel_mess.doctype.mess_bill_run import mess_bill_run as module

		fees, notify = self.publishing()
		with (
			patch.object(module, "BACKGROUND_THRESHOLD", 0),
			patch.object(module, "is_scheduler_inactive", return_value=False),
			patch("frappe.enqueue") as enqueue,
		):
			run = frappe.get_doc("Mess Bill Run", self.run.name)
			result = run.submit() if action == "submit" else run.cancel()
		self.assertEqual(result, {"queued": True})
		frappe.db.commit()  # the request that queued the work ends here
		job = {k: v for k, v in enqueue.call_args.kwargs.items() if k in ("name", "mess", "action", "user")}
		with fees, notify, patch("frappe.publish_realtime") as realtime:
			module.run_publishing_action_in_background(**job)
		return realtime

	def test_background_publish_and_cancel(self):
		realtime = self.run_worker("submit")
		run = frappe.get_doc("Mess Bill Run", self.run.name)
		self.assertEqual((run.docstatus, run.status), (1, "Published"))
		self.assertFalse(run.is_locked)
		self.assertEqual(
			getdate(frappe.db.get_value("Hostel Mess", self.mess, "billed_upto")), getdate(PERIOD[1])
		)
		self.assertIn("mess_bill_run_action_done", [c.args[0] for c in realtime.call_args_list])

		realtime = self.run_worker("cancel")
		run.reload()
		self.assertEqual((run.docstatus, run.status), (2, "Cancelled"))
		self.assertIsNone(frappe.db.get_value("Hostel Mess", self.mess, "billed_upto"))
		self.assertIn("mess_bill_run_action_done", [c.args[0] for c in realtime.call_args_list])

	def test_background_failure_is_reported(self):
		frappe.get_doc("Mess Expense", self.late_expense).submit()
		frappe.db.commit()
		realtime = self.run_worker("submit")
		failure = next(c for c in realtime.call_args_list if c.args[0] == "mess_bill_run_action_failed")
		self.assertIn("Compute Bills again", failure.args[1]["error"])
		run = frappe.get_doc("Mess Bill Run", self.run.name)
		self.assertEqual(run.docstatus, 0)
		self.assertFalse(run.is_locked)
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{"reference_doctype": "Mess Bill Run", "reference_name": run.name, "comment_type": "Comment"},
			)
		)
		frappe.db.delete("Comment", {"reference_doctype": "Mess Bill Run", "reference_name": run.name})
		frappe.db.commit()
