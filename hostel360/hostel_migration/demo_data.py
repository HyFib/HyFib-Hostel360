"""Demonstration and UAT data for a Tamil Nadu engineering college hostel.

``generate`` builds a realistic, self-consistent data set: two boys' hostels and one
girls' hostel with blocks, floors, rooms and beds; students with guardians and Tamil
names from the southern districts; allotments; this year's fee structure with invoices
and a mix of paid, part-paid and unpaid dues; mess items, last month's and this month's
stock and expenses, menus for last, this and next week, approved and pending mess
reductions and last month's published mess bills; leaves in every state (some of them
today), grievances (one escalated), bonafide requests pending and issued, monthly
feedback; and for the operations side support tickets, an alert history, monitoring
samples, backup records and last month's performance report.

The output depends only on ``seed`` and ``students``. Running it again adds nothing that
already exists (records are recognised by their natural keys or through Migration Record
Map entries under ``demo_*`` entities), so a larger ``students`` value simply tops up.

Demo register numbers start with ``UAT`` and e-mail addresses use the reserved
``.invalid`` domain, so demo records are easy to spot and no e-mail is ever sent to
them. Mobile numbers are random; the builder refuses to run while SMS or WhatsApp is
live (not in test mode) so scheduled reminders cannot reach real phones.
"""

import datetime
import random

import frappe
from frappe import _
from frappe.utils import add_days, add_months, cint, flt, get_datetime, getdate, now_datetime, nowdate

from hostel360.hostel_migration.doctype.migration_record_map.migration_record_map import make_map_key
from hostel360.hostel_migration.engine import MAP_DOCTYPE

DEMO_DOMAIN = "demo.hostel360.invalid"
# Migration Record Map namespace for records made here.
DEMO_SOURCE_SYSTEM = "hostel360-demo"
CAMPUS_ADDRESS = "ACGCET Campus, Karaikudi – 630 003, Sivagangai District, Tamil Nadu"  # noqa: RUF001 - en dash as printed

DEPARTMENTS = (
	("Civil Engineering", "CE"),
	("Mechanical Engineering", "ME"),
	("Electrical and Electronics Engineering", "EE"),
	("Electronics and Communication Engineering", "EC"),
	("Computer Science and Engineering", "CS"),
)
PROGRAMS = (
	*((f"B.E. {name}", name, "UG", 4) for name, _code in DEPARTMENTS),
	("M.E. Structural Engineering", "Civil Engineering", "PG", 2),
	("M.E. Computer Science and Engineering", "Computer Science and Engineering", "PG", 2),
)

HOSTELS = (
	# code, name, gender, blocks
	("BH1", "Boys Hostel I", "Male", ("A", "B")),
	("BH2", "Boys Hostel II", "Male", ("A",)),
	("GH1", "Girls Hostel", "Female", ("A", "B")),
)
FLOORS_PER_BLOCK = 3
HOSTEL_FACILITIES = ("Wi-Fi", "RO Drinking Water", "Hot Water", "Power Backup")
ROOM_FACILITIES = ("Cot", "Mattress", "Study Table", "Chair", "Cupboard", "Ceiling Fan", "Lighting")

MALE_NAMES = (
	"Arun",
	"Karthik",
	"Vignesh",
	"Surya",
	"Prakash",
	"Dinesh",
	"Saravanan",
	"Manikandan",
	"Ramesh",
	"Gokul",
	"Harish",
	"Balaji",
	"Senthil",
	"Muthu",
	"Aravind",
	"Ajith",
	"Vijay",
	"Naveen",
	"Santhosh",
	"Sathish",
	"Bharath",
	"Kishore",
	"Lokesh",
	"Madhan",
	"Pradeep",
	"Ragul",
	"Sakthi",
	"Tamilselvan",
	"Vasanth",
	"Yuvaraj",
	"Ezhil",
	"Ilango",
	"Jeeva",
	"Kathir",
	"Nithish",
	"Pandi",
	"Selvam",
	"Hariharan",
)
FEMALE_NAMES = (
	"Priya",
	"Divya",
	"Kavya",
	"Lakshmi",
	"Meena",
	"Anitha",
	"Deepa",
	"Revathi",
	"Sangeetha",
	"Keerthana",
	"Nandhini",
	"Swathi",
	"Abinaya",
	"Gayathri",
	"Harini",
	"Janani",
	"Kaviya",
	"Monisha",
	"Pavithra",
	"Ramya",
	"Sowmiya",
	"Thenmozhi",
	"Vaishnavi",
	"Yamini",
	"Aishwarya",
	"Bhavani",
	"Dharani",
	"Ilakkiya",
)
FATHER_NAMES = (
	"Murugan",
	"Kumar",
	"Selvaraj",
	"Pandian",
	"Subramanian",
	"Krishnan",
	"Natarajan",
	"Ramasamy",
	"Velu",
	"Ganesan",
	"Shanmugam",
	"Rajendran",
	"Palanisamy",
	"Arumugam",
	"Chinnasamy",
	"Kannan",
	"Sundaram",
	"Mohan",
	"Periyasamy",
	"Raman",
	"Thangaraj",
	"Veerappan",
	"Karuppiah",
	"Sekar",
)
MOTHER_NAMES = (
	"Selvi",
	"Lakshmi",
	"Valli",
	"Meenakshi",
	"Kamala",
	"Saraswathi",
	"Vijaya",
	"Chitra",
	"Malathi",
	"Rani",
	"Jothi",
	"Padma",
	"Shanthi",
	"Bhuvana",
	"Kalaivani",
	"Amudha",
)
OCCUPATIONS = (
	"Farmer",
	"Teacher",
	"Government Employee",
	"Business",
	"Driver",
	"Shop Owner",
	"Electrician",
	"Bank Employee",
	"Contractor",
	"Private Employee",
	"Weaver",
	"Fisherman",
)
PLACES = (
	# city, district, pincode
	("Karaikudi", "Sivagangai", "630001"),
	("Sivagangai", "Sivagangai", "630561"),
	("Devakottai", "Sivagangai", "630302"),
	("Madurai", "Madurai", "625001"),
	("Melur", "Madurai", "625106"),
	("Ramanathapuram", "Ramanathapuram", "623501"),
	("Paramakudi", "Ramanathapuram", "623707"),
	("Virudhunagar", "Virudhunagar", "626001"),
	("Sivakasi", "Virudhunagar", "626123"),
	("Pudukkottai", "Pudukkottai", "622001"),
	("Aranthangi", "Pudukkottai", "614616"),
	("Thanjavur", "Thanjavur", "613001"),
	("Kumbakonam", "Thanjavur", "612001"),
	("Tiruchirappalli", "Tiruchirappalli", "620001"),
	("Dindigul", "Dindigul", "624001"),
	("Theni", "Theni", "625531"),
	("Tirunelveli", "Tirunelveli", "627001"),
	("Thoothukudi", "Thoothukudi", "628001"),
	("Nagercoil", "Kanniyakumari", "629001"),
	("Coimbatore", "Coimbatore", "641001"),
	("Salem", "Salem", "636001"),
	("Chennai", "Chennai", "600001"),
)
STREETS = (
	"Gandhi Street",
	"Nehru Nagar",
	"Anna Salai",
	"Kamarajar Street",
	"Bharathi Nagar",
	"Main Road",
	"Periyar Nagar",
	"Vivekananda Street",
	"Thiruvalluvar Street",
	"Mariamman Kovil Street",
)
BLOOD_GROUPS = ("O+", "O+", "B+", "B+", "A+", "A+", "AB+", "O-", "B-", "A-")

FEE_ITEMS_SENIOR = (
	("Hostel Rent", 15000),
	("Establishment Charges", 6000),
	("Electricity Charges", 3000),
)
FEE_ITEMS_FIRST_YEAR = (*FEE_ITEMS_SENIOR, ("Caution Deposit", 5000))

SUPPLIERS = (
	("Sri Murugan Provision Stores", "K. Murugesan", "Karaikudi"),
	("Chettinad Vegetable Traders", "S. Ravi", "Karaikudi"),
	("Karaikudi Milk Producers Co-operative", "Secretary", "Karaikudi"),
	("Sivagangai Gas Agency", "Manager", "Sivagangai"),
)
# item, qty received at the start of the month, rate (INR), supplier index
STOCK_RECEIPT = (
	("Ponni Rice (Boiled)", 900, 52, 0),
	("Idli Rice", 300, 44, 0),
	("Toor Dal", 90, 148, 0),
	("Urad Dal", 70, 132, 0),
	("Sunflower Oil", 80, 145, 0),
	("Sugar", 40, 44, 0),
	("Tea Powder", 8, 420, 0),
	("Tamarind", 15, 160, 0),
	("Chilli Powder", 8, 320, 0),
	("Onion", 120, 38, 1),
	("Tomato", 80, 30, 1),
	("Potato", 70, 34, 1),
	("Milk", 600, 44, 2),
	("Eggs", 1500, 6, 0),
	("LPG Cylinder (Commercial 19 kg)", 6, 1780, 3),
)
# share of the receipt issued per demo issue day
DAILY_ISSUE_SHARE = 0.025
COUNTED_UOMS = ("Nos", "Packet", "Dozen", "Cylinder")

MENU = {
	"Breakfast": (
		"Idli, Sambar, Coconut Chutney",
		"Pongal, Vadai, Sambar, Chutney",
		"Dosa, Tomato Chutney, Sambar",
		"Poori, Potato Masala",
		"Upma, Coconut Chutney, Banana",
		"Idiyappam, Vegetable Kurma",
		"Masala Dosa, Sambar, Chutney",
	),
	"Lunch": (
		"Rice, Sambar, Rasam, Beans Poriyal, Curd, Appalam",
		"Rice, Vathal Kuzhambu, Rasam, Cabbage Poriyal, Curd",
		"Rice, Mor Kuzhambu, Rasam, Potato Fry, Curd",
		"Rice, Sambar, Rasam, Kootu, Curd, Pickle",
		"Lemon Rice, Curd Rice, Potato Chips, Pickle",
		"Rice, Kara Kuzhambu, Rasam, Keerai Poriyal, Curd",
		"Vegetable Biryani, Onion Raita, Gulab Jamun",
	),
	"Snacks": (
		"Tea, Sundal",
		"Coffee, Onion Bajji",
		"Tea, Biscuits",
		"Coffee, Masala Vadai",
		"Tea, Banana Bajji",
		"Tea, Sweet Corn",
		"Coffee, Samosa",
	),
	"Dinner": (
		"Chapati, Vegetable Kurma, Rice, Rasam",
		"Parotta, Vegetable Salna, Curd Rice",
		"Chapati, Channa Masala, Rice, Rasam",
		"Idli, Sambar, Chutney, Egg Masala",
		"Dosa, Tomato Chutney, Rice, Rasam",
		"Chapati, Dal Fry, Curd Rice",
		"Chicken Biryani / Paneer Biryani, Raita",
	),
}

GRIEVANCES = (
	(
		"Plumbing",
		"Tap leaking in second floor bathroom",
		"The tap near the wash basin has been leaking for three days and water collects on the floor.",
		"Medium",
		"Resolved",
		"Washer replaced by the plumber.",
	),
	(
		"Electrical",
		"Ceiling fan not working",
		"The fan in our room stopped working last night. The regulator is also loose.",
		"High",
		"In Progress",
		None,
	),
	(
		"Internet",
		"Wi-Fi very slow in B block",
		"Wi-Fi in B block is too slow to download lecture notes in the evening.",
		"Medium",
		"Open",
		None,
	),
	(
		"Mess and Food",
		"Sambar too watery at dinner",
		"Dinner sambar has been very thin this week. Please check the quantity of dal used.",
		"Low",
		"Resolved",
		"Discussed with the mess committee; dal quantity corrected.",
	),
	(
		"Water",
		"No drinking water on ground floor",
		"The RO water point on the ground floor has not been working since Monday.",
		"High",
		"Open",
		None,
	),
	(
		"Cleanliness",
		"Corridor not cleaned",
		"The first floor corridor was not cleaned on Saturday and Sunday.",
		"Low",
		"Closed",
		"Housekeeping roster revised.",
	),
	(
		"Security",
		"Gate left open after 10 pm",
		"The side gate near the cycle stand was found open after 10 pm on two nights this week.",
		"High",
		"Escalated",
		None,
	),
	(
		"Room and Furniture",
		"Cupboard lock broken again",
		"The cupboard lock that was replaced last week is broken again.",
		"Medium",
		"Reopened",
		None,
	),
	(
		"Mess and Food",
		"Breakfast served late on Monday",
		"Breakfast was served only at 8:20 on Monday, so students with 8:30 classes missed it.",
		"Medium",
		"In Progress",
		None,
	),
)
LEAVE_REASONS = (
	("Pongal holidays with family", "Home"),
	("Sister's marriage", "Home"),
	("Temple festival in native village", "Home"),
	("Medical check-up", "Madurai"),
	("Inter-college sports meet", "Tiruchirappalli"),
)
MESSES = (("Boys Mess", ("BH1", "BH2"), 0.65), ("Girls Mess", ("GH1",), 0.35))
REDUCTION_REASONS = (
	("Home Visit", "Going home for the temple festival."),
	("Medical", "Advised rest at home after fever."),
	("Internship", "Industrial visit and in-plant training at Tiruchirappalli."),
	("Sports or Cultural Event", "Representing the college at the zonal sports meet."),
)
FEEDBACK_COMMENTS = (
	"Breakfast is good. Please serve the sambar hot at dinner.",
	"Rooms are cleaned regularly. Wi-Fi is slow after 9 pm.",
	"Sunday special lunch was very good.",
	"Please add one more fruit day in the week.",
	"Water cooler on the second floor needs servicing.",
	"Staff are polite and quick to help.",
	"",
)
# key, title, severity, category, source, days ago, minutes open, occurrences, outage, description
ALERT_HISTORY = (
	(
		"uptime.portal.unreachable",
		"Portal did not answer the uptime probe",
		"Critical",
		"Availability",
		"Uptime Probe",
		23,
		7,
		3,
		1,
		"The probe could not reach https://hostel360 for 7 minutes while the application was restarted after an update.",
	),
	(
		"messaging-msg91-failing",
		"MSG91 messages are failing",
		"Warning",
		"Messaging",
		"App",
		17,
		42,
		6,
		0,
		"The SMS gateway answered with HTTP 503 for six messages in a row; it recovered on its own.",
	),
	(
		"payments.reconciliation.unreachable",
		"Payment gateway could not be reached for reconciliation",
		"Warning",
		"Payments",
		"App",
		12,
		25,
		2,
		0,
		"Two reconciliation runs could not reach the gateway; the next run settled every open payment.",
	),
	(
		"waf.blocks.spike",
		"Spike in requests blocked at the firewall",
		"Info",
		"Security",
		"Host Agent",
		9,
		18,
		1,
		0,
		"An automated scanner was blocked 320 times in 15 minutes and then banned for an hour.",
	),
	(
		"backup.daily.failed",
		"Daily backup failed on hostel360-app",
		"Critical",
		"Backup",
		"Host Agent",
		6,
		95,
		1,
		0,
		"Object storage did not answer within 90 seconds. The backup was run again and succeeded.",
	),
	(
		"health.queue.backlog",
		"Background job backlog above 500",
		"Warning",
		"Jobs",
		"Health Check",
		3,
		12,
		4,
		0,
		"Fee reminders for all students were queued at once; the workers cleared them in 12 minutes.",
	),
)
# subject, category, priority, status, hours ago, description, resolution
TICKETS = (
	(
		"Parent cannot sign in to the portal",
		"Access or Login",
		"High",
		"Resolved",
		26 * 24,
		"A parent says the sign-in code does not arrive on her phone.",
		"The number on the guardian record had a typing mistake. Corrected; the parent signed in.",
	),
	(
		"Receipt shows the wrong academic year",
		"Bug",
		"Medium",
		"Closed",
		15 * 24,
		"Receipts printed this morning show last year's academic year in the header.",
		"Hostel Settings pointed at last year's academic year. Changed and the receipts reprinted.",
	),
	(
		"Correct a student's room after transfer",
		"Data Correction",
		"Medium",
		"In Progress",
		30,
		"A student was moved from room A102 to A205 but the old room still shows on the portal.",
		None,
	),
	(
		"Add a new fee head for the hostel day celebration",
		"Change Request",
		"Low",
		"Waiting on Institution",
		50,
		"The office wants a separate fee head for the hostel day contribution.",
		None,
	),
	(
		"How do we publish the mess bill for a partial month?",
		"How To",
		"Low",
		"Open",
		2,
		"The girls' mess was closed for five days. How should the bill run be done?",
		None,
	),
)
DEMO_HOST = "hostel360-app"


class DemoDataError(frappe.ValidationError):
	pass


class DemoDataBuilder:
	def __init__(self, students: int, seed: int, progress=None):
		if students < 1 or students > 5000:
			frappe.throw(_("Choose between 1 and 5,000 demo students."))
		self.count = students
		self.seed = seed
		self.rng = random.Random(seed)
		self.progress = progress
		self.summary: dict[str, int] = {}
		self.errors: list[str] = []
		self.academic_year = None
		self.year_start = None
		self.used_mobiles: set[str] = set()
		# Monthly provisions in STOCK_RECEIPT feed about sixty residents.
		self.scale = max(1.0, students / 60)

	# ---------------------------------------------------------------- utilities

	def say(self, message: str) -> None:
		if self.progress:
			self.progress(message)

	def bump(self, key: str, by: int = 1) -> None:
		self.summary[key] = self.summary.get(key, 0) + by

	def safely(self, label: str, fn, *args, **kwargs):
		"""Run one record's creation in a savepoint; a failure is reported, not fatal."""
		savepoint = f"h360_demo_{frappe.generate_hash(length=8)}"
		frappe.db.savepoint(savepoint)
		try:
			result = fn(*args, **kwargs)
		except Exception as e:
			frappe.db.rollback(save_point=savepoint)
			frappe.clear_messages()
			self.errors.append(f"{label}: {frappe.utils.strip_html(str(e)) or e.__class__.__name__}")
			return None
		frappe.db.release_savepoint(savepoint)
		return result

	def demo_record(self, entity: str, source_id: str) -> str | None:
		return frappe.db.get_value(
			MAP_DOCTYPE, {"map_key": make_map_key(DEMO_SOURCE_SYSTEM, entity, source_id)}, "target_name"
		)

	def remember(self, entity: str, source_id: str, doctype: str, name: str) -> None:
		doc = frappe.get_doc(
			{
				"doctype": MAP_DOCTYPE,
				"source_system": DEMO_SOURCE_SYSTEM,
				"source_entity": entity,
				"source_id": source_id,
				"target_doctype": doctype,
				"target_name": name,
				"row_checksum": "demo",
				"last_synced_on": now_datetime(),
			}
		)
		doc.flags.ignore_permissions = True
		doc.insert()

	def mobile(self) -> str:
		# Only the builder's own state decides the number, so a rerun draws exactly the
		# same sequence; a clash with an existing record fails that one record visibly.
		while True:
			number = str(self.rng.choice((6, 7, 8, 9))) + "".join(
				str(self.rng.randrange(10)) for _i in range(9)
			)
			if number not in self.used_mobiles:
				self.used_mobiles.add(number)
				return number

	# ---------------------------------------------------------------- run

	def check_messaging(self) -> None:
		from hostel360.demo import is_demo_site

		if is_demo_site() or not frappe.db.exists("DocType", "Messaging Settings"):
			# A demonstration site sends nothing at all, whatever Messaging Settings say.
			return
		settings = frappe.get_cached_doc("Messaging Settings")
		live = (cint(settings.get("sms_enabled")) or cint(settings.get("whatsapp_enabled"))) and not cint(
			settings.get("test_mode")
		)
		if live:
			raise DemoDataError(
				_(
					"SMS or WhatsApp is live on this site. Turn on Test Mode in Messaging Settings before creating demo data, so reminders never reach real phones."
				)
			)

	def run(self, steps=None) -> dict:
		self.check_messaging()
		previous = (frappe.flags.hostel360_migration, frappe.flags.mute_emails, frappe.session.user)
		frappe.flags.hostel360_migration = True
		frappe.flags.mute_emails = True
		frappe.set_user("Administrator")
		try:
			for step in steps or (
				self.setup_masters,
				self.setup_hostels,
				self.create_students,
				self.allot_rooms,
				self.create_fees,
				self.setup_mess,
				self.create_menus,
				self.create_services,
				self.create_reductions,
				self.create_bill_runs,
				self.create_feedback,
				self.create_indents,
				self.create_operations,
			):
				step()
				frappe.db.commit()  # nosemgrep
		finally:
			frappe.flags.hostel360_migration, frappe.flags.mute_emails, user = previous
			frappe.set_user(user)
		return {"students": self.count, "seed": self.seed, "created": self.summary, "errors": self.errors}

	# ---------------------------------------------------------------- masters

	def setup_masters(self) -> None:
		self.say(_("Academic year, departments and programmes"))
		from hostel360.hostel_setup.setup import ensure_current_academic_year

		self.academic_year = (
			frappe.db.get_single_value("Hostel Settings", "current_academic_year")
			or ensure_current_academic_year()
		)
		self.year_start = getdate(
			frappe.db.get_value("Hostel Academic Year", self.academic_year, "start_date")
		)

		for name, code in DEPARTMENTS:
			if not frappe.db.exists("Academic Department", name):
				frappe.get_doc(
					{"doctype": "Academic Department", "department_name": name, "department_code": code}
				).insert(ignore_permissions=True)
				self.bump("Academic Department")
		for name, department, level, years in PROGRAMS:
			if not frappe.db.exists("Academic Program", name):
				frappe.get_doc(
					{
						"doctype": "Academic Program",
						"program_name": name,
						"department": department,
						"degree_level": level,
						"duration_years": years,
					}
				).insert(ignore_permissions=True)
				self.bump("Academic Program")

	def setup_hostels(self) -> None:
		self.say(_("Hostels, blocks, floors and rooms"))
		boys = round(self.count * 0.65)
		beds_needed = {"Male": boys, "Female": self.count - boys}
		facilities = [f for f in HOSTEL_FACILITIES if frappe.db.exists("Room Facility", f)]
		room_facilities = [f for f in ROOM_FACILITIES if frappe.db.exists("Room Facility", f)]

		for code, name, gender, blocks in HOSTELS:
			if not frappe.db.exists("Hostel", code):
				frappe.get_doc(
					{
						"doctype": "Hostel",
						"hostel_code": code,
						"hostel_name": name,
						"gender": gender,
						"address": CAMPUS_ADDRESS,
						"is_active": 1,
						"facilities": [{"facility": f} for f in facilities],
					}
				).insert(ignore_permissions=True)
				self.bump("Hostel")

			same_gender = [h for h in HOSTELS if h[2] == gender]
			share = beds_needed[gender] / len(same_gender)
			# About a fifth of the beds stay free for transfers and late admissions.
			rooms_per_floor = max(3, -(-int(share * 1.25) // (len(blocks) * FLOORS_PER_BLOCK * 3)))

			for block_code in blocks:
				block = f"{code}-{block_code}"
				if not frappe.db.exists("Hostel Block", block):
					frappe.get_doc(
						{
							"doctype": "Hostel Block",
							"hostel": code,
							"block_code": block_code,
							"block_name": f"{block_code} Block",
						}
					).insert(ignore_permissions=True)
					self.bump("Hostel Block")
				for floor_number in range(FLOORS_PER_BLOCK):
					floor = f"{block}-F{floor_number}"
					if not frappe.db.exists("Hostel Floor", floor):
						frappe.get_doc(
							{"doctype": "Hostel Floor", "block": block, "floor_number": floor_number}
						).insert(ignore_permissions=True)
						self.bump("Hostel Floor")
					for idx in range(1, rooms_per_floor + 1):
						room_number = f"{block_code}{floor_number}{idx:02d}"
						if frappe.db.exists("Hostel Room", f"{code}-{room_number}"):
							continue
						room_type, capacity = ("Double", 2) if idx == 1 else ("Triple", 3)
						frappe.get_doc(
							{
								"doctype": "Hostel Room",
								"hostel": code,
								"block": block,
								"floor": floor,
								"room_number": room_number,
								"room_type": room_type,
								"capacity": capacity,
								"facilities": [{"facility": f} for f in room_facilities],
							}
						).insert(ignore_permissions=True)
						self.bump("Hostel Room")

	# ---------------------------------------------------------------- people

	def create_students(self) -> None:
		self.say(_("Students and guardians"))
		programs = [p for p in PROGRAMS if p[2] == "UG"]
		pg_programs = [p for p in PROGRAMS if p[2] == "PG"]
		boys = round(self.count * 0.65)
		for seq in range(1, self.count + 1):
			# Draw every random value for this student first, so the sequence (and
			# therefore every later student) is the same whether or not it exists.
			gender = "Male" if seq <= boys else "Female"
			person = frappe._dict(
				seq=seq,
				gender=gender,
				first=self.rng.choice(MALE_NAMES if gender == "Male" else FEMALE_NAMES),
				father=self.rng.choice(FATHER_NAMES),
				mother=self.rng.choice(MOTHER_NAMES),
			)
			is_pg = self.rng.random() < 0.08
			person.program = self.rng.choice(pg_programs if is_pg else programs)
			person.year_of_study = self.rng.randint(1, 2 if is_pg else 4)
			person.city, person.district, person.pincode = self.rng.choice(PLACES)
			person.address = f"{self.rng.randint(1, 240)}, {self.rng.choice(STREETS)}"
			person.blood_group = self.rng.choice(BLOOD_GROUPS)
			person.occupation = self.rng.choice(OCCUPATIONS)
			has_mother = self.rng.random() < 0.3
			birth_offset = self.rng.randint(0, 364)
			person.student_mobile, person.father_mobile = self.mobile(), self.mobile()
			person.mother_mobile = self.mobile() if has_mother else None

			person.admission_year = self.year_start.year - (person.year_of_study - 1)
			dept_code = dict(DEPARTMENTS).get(person.program[1], "GN")
			person.register_number = f"UAT{person.admission_year % 100:02d}{dept_code}{seq:04d}"
			birth_year = person.admission_year - (22 if is_pg else 18)
			person.date_of_birth = add_days(datetime.date(birth_year, 1, 1), birth_offset)

			if frappe.db.exists("Hostel Student", person.register_number):
				continue
			self.safely(f"Student {person.register_number}", self.create_student, person)

	def create_guardian(self, name: str, relation: str, mobile: str, occupation: str, person) -> str:
		doc = frappe.get_doc(
			{
				"doctype": "Hostel Guardian",
				"guardian_name": name,
				"relation": relation,
				"mobile_number": mobile,
				"occupation": occupation,
				"address": f"{person.address}, {person.city}, {person.district} District – {person.pincode}",  # noqa: RUF001
				"validation_status": "Validated",
			}
		).insert(ignore_permissions=True)
		self.bump("Hostel Guardian")
		return doc.name

	def create_student(self, person) -> None:
		guardians = [
			{
				"guardian": self.create_guardian(
					person.father, "Father", person.father_mobile, person.occupation, person
				),
				"relation": "Father",
				"is_primary": 1,
				"can_approve_leave": 1,
			}
		]
		if person.mother_mobile:
			guardians.append(
				{
					"guardian": self.create_guardian(
						person.mother, "Mother", person.mother_mobile, "Homemaker", person
					),
					"relation": "Mother",
					"is_primary": 0,
					"can_approve_leave": 1,
				}
			)

		student = frappe.get_doc(
			{
				"doctype": "Hostel Student",
				"register_number": person.register_number,
				"roll_number": person.register_number[3:],
				"first_name": person.first,
				"last_name": person.father,
				"gender": person.gender,
				"date_of_birth": person.date_of_birth,
				"blood_group": person.blood_group,
				"program": person.program[0],
				"year_of_study": str(person.year_of_study),
				"admission_year": person.admission_year,
				"academic_year": self.academic_year,
				"mobile_number": person.student_mobile,
				"email": f"{person.first}.{person.father}{person.seq}@{DEMO_DOMAIN}".lower(),
				"address_line1": person.address,
				"city": person.city,
				"district": person.district,
				"state": "Tamil Nadu",
				"pincode": person.pincode,
				"emergency_contact_name": person.father,
				"emergency_contact_phone": person.father_mobile,
				"guardians": guardians,
				"status": "Active",
				"mess_enrolled": 1,
				"validation_status": "Validated",
				"validated_by": "Administrator",
				"validated_on": get_datetime(add_days(self.year_start, 7)),
			}
		)
		student.insert(ignore_permissions=True)
		self.remember("demo_students", person.register_number, "Hostel Student", student.name)
		self.bump("Hostel Student")

	def allot_rooms(self) -> None:
		self.say(_("Room allotments"))
		gender_of_hostel = {code: gender for code, _name, gender, _blocks in HOSTELS}
		students = frappe.get_all(
			"Hostel Student",
			filters={"register_number": ["like", "UAT%"], "status": "Active"},
			fields=["name", "gender", "current_allotment"],
			order_by="name asc",
			limit_page_length=0,
		)
		from_date = add_days(self.year_start, 14)
		for student in students:
			# Every tenth student is a day scholar this year and has no bed.
			if student.current_allotment or cint(student.name[-4:]) % 10 == 0:
				continue
			hostels = [code for code, gender in gender_of_hostel.items() if gender == student.gender]
			vacant = {"status": "Vacant", "is_active": 1, "current_allotment": ["is", "not set"]}

			def free_share(hostel: str, vacant=vacant) -> tuple:
				beds = frappe.db.count("Hostel Bed", {"is_active": 1, "hostel": hostel}) or 1
				return (frappe.db.count("Hostel Bed", {**vacant, "hostel": hostel}) / beds, hostel)

			# Fill the hostel with the largest share of free beds first, so both boys' hostels fill
			# up alike however many blocks each has.
			hostel = max(hostels, key=free_share)
			bed = frappe.db.get_value("Hostel Bed", {**vacant, "hostel": hostel}, "name", order_by="name asc")
			if not bed:
				self.errors.append(f"No vacant bed for {student.name}")
				continue

			def allot(student=student.name, bed=bed):
				doc = frappe.get_doc(
					{
						"doctype": "Room Allotment",
						"student": student,
						"bed": bed,
						"allotment_type": "New",
						"academic_year": self.academic_year,
						"from_date": from_date,
						"remarks": _("Demo allotment"),
					}
				)
				doc.insert(ignore_permissions=True)
				doc.submit()
				self.bump("Room Allotment")

			self.safely(f"Allotment {student.name}", allot)

	# ---------------------------------------------------------------- fees

	def fee_structure(self, label: str, items, year_of_study: str | None, due_date) -> str:
		name = f"Hostel Fee {self.academic_year} ({label})"
		if not frappe.db.exists("Hostel Fee Structure", name):
			frappe.get_doc(
				{
					"doctype": "Hostel Fee Structure",
					"structure_name": name,
					"academic_year": self.academic_year,
					"due_date": due_date,
					"year_of_study": year_of_study or "",
					"is_active": 1,
					"items": [{"fee_head": head, "amount": amount} for head, amount in items],
					"notes": _("Demo fee structure"),
				}
			).insert(ignore_permissions=True)
			self.bump("Hostel Fee Structure")
		return name

	def create_fees(self) -> None:
		self.say(_("Fee structure, invoices and receipts"))
		self.posting_date = add_days(self.year_start, 20)
		self.due_date = add_days(self.year_start, 60)
		structures = {
			"first": self.fee_structure("First Year", FEE_ITEMS_FIRST_YEAR, "1", self.due_date),
			"senior": self.fee_structure("Senior", FEE_ITEMS_SENIOR, None, self.due_date),
		}
		students = frappe.get_all(
			"Hostel Student",
			filters={"register_number": ["like", "UAT%"], "current_allotment": ["is", "set"]},
			fields=["name", "year_of_study", "current_hostel"],
			order_by="name asc",
			limit_page_length=0,
		)
		for student in students:
			first_year = student.year_of_study == "1"
			structure = structures["first" if first_year else "senior"]
			items = FEE_ITEMS_FIRST_YEAR if first_year else FEE_ITEMS_SENIOR
			# The draws happen for every student so reruns stay in step.
			draw = frappe._dict(
				roll=self.rng.random(),
				mode=self.rng.choice(("UPI", "UPI", "Bank Transfer", "Cash", "Demand Draft")),
				paid_after=self.rng.randint(1, 40),
				reference="".join(str(self.rng.randrange(10)) for _i in range(12)),
			)
			if frappe.db.exists(
				"Hostel Fee Invoice",
				{"student": student.name, "fee_structure": structure, "docstatus": ["<", 2]},
			):
				continue
			self.safely(f"Fees {student.name}", self.bill_student, student, structure, items, draw)

	def bill_student(self, student, structure: str, items, draw) -> None:
		from hostel360.hostel_fees.desk import record_payment

		doc = frappe.get_doc(
			{
				"doctype": "Hostel Fee Invoice",
				"student": student.name,
				"invoice_type": "Hostel Fee",
				"academic_year": self.academic_year,
				"fee_structure": structure,
				"hostel": student.current_hostel,
				"posting_date": self.posting_date,
				"due_date": self.due_date,
				"items": [{"fee_head": head, "amount": amount} for head, amount in items],
				# Nothing was collected before this data set existed; say so explicitly,
				# since historical-data mode accepts whatever amount is supplied.
				"migrated_paid_amount": 0,
				"remarks": _("Demo invoice"),
			}
		)
		doc.flags.ignore_permissions = True
		doc.insert()
		doc.submit()
		invoice = doc.name
		self.bump("Hostel Fee Invoice")

		# Six in ten have paid in full, a few paid half, the rest still owe.
		total = sum(amount for _head, amount in items)
		if draw.roll < 0.6:
			amount = total
		elif draw.roll < 0.75:
			amount = flt(total / 2, 0)
		else:
			return
		payment_date = min(getdate(add_days(self.posting_date, draw.paid_after)), getdate(nowdate()))
		cash = draw.mode == "Cash"
		record_payment(
			student.name,
			draw.mode,
			[{"invoice": invoice, "allocated_amount": amount}],
			payment_date=str(payment_date),
			reference_no=None if cash else f"{draw.mode[:3].upper()}{draw.reference}",
			reference_date=None if cash else str(payment_date),
			bank_name="State Bank of India" if draw.mode in ("Bank Transfer", "Demand Draft") else None,
			remarks=_("Demo receipt"),
		)
		self.bump("Hostel Payment")

	# ---------------------------------------------------------------- calendar helpers

	@property
	def today(self) -> datetime.date:
		return getdate(nowdate())

	def month_start(self, months_back: int = 0) -> datetime.date:
		return getdate(add_months(self.today.replace(day=1), -months_back))

	def monday(self, weeks_ahead: int = 0) -> datetime.date:
		return (
			self.today - datetime.timedelta(days=self.today.weekday()) + datetime.timedelta(weeks=weeks_ahead)
		)

	def draw(self, *key) -> random.Random:
		"""A generator of its own for one record, so the main sequence (students, fees) stays
		the same however many of the later records already exist."""
		return random.Random(":".join(str(part) for part in (self.seed, *key)))

	def residents(self) -> list:
		return frappe.get_all(
			"Hostel Student",
			filters={"register_number": ["like", "UAT%"], "current_allotment": ["is", "set"]},
			fields=["name", "current_hostel", "current_room", "mobile_number", "gender"],
			order_by="name asc",
		)

	def isolated(self, label: str, fn, *args, **kwargs):
		"""Run a step that may commit on its own (billing, reports) outside any savepoint."""
		try:
			result = fn(*args, **kwargs)
		except Exception as e:
			frappe.db.rollback()
			frappe.clear_messages()
			self.errors.append(f"{label}: {frappe.utils.strip_html(str(e)) or e.__class__.__name__}")
			return None
		frappe.db.commit()  # nosemgrep
		return result

	# ---------------------------------------------------------------- mess

	def setup_mess(self) -> None:
		self.say(_("Mess, suppliers, stock and expenses"))
		for name, hostels, _share in MESSES:
			if not frappe.db.exists("Hostel Mess", name):
				frappe.get_doc(
					{
						"doctype": "Hostel Mess",
						"mess_name": name,
						"is_active": 1,
						"hostels": [{"hostel": h} for h in hostels],
						"description": _("Demo mess"),
					}
				).insert(ignore_permissions=True)
				self.bump("Hostel Mess")

		for supplier, contact, city in SUPPLIERS:
			if not frappe.db.exists("Mess Supplier", supplier):
				frappe.get_doc(
					{
						"doctype": "Mess Supplier",
						"supplier_name": supplier,
						"contact_person": contact,
						"address": f"{city}, Tamil Nadu",
						"is_active": 1,
					}
				).insert(ignore_permissions=True)
				self.bump("Mess Supplier")

		this_month, last_month = self.month_start(0), self.month_start(1)
		yesterday = getdate(add_days(self.today, -1))
		periods = (
			(last_month, getdate(add_days(this_month, -1)), True),
			(this_month, yesterday, False),
		)
		for name, _hostels, share in MESSES:
			for start, end, closed in periods:
				if end < start:
					# The 1st of the month: nothing has been received for it yet.
					continue
				self.safely(f"Stock receipt {name} {start}", self.stock_receipt, name, share, start)
				day = getdate(add_days(start, 1))
				while day <= end:
					self.safely(f"Stock issue {name} {day}", self.stock_issue, name, share, day)
					day = getdate(add_days(day, 1))
				self.safely(
					f"Mess expenses {name} {start}", self.mess_expenses, name, share, start, end, closed
				)

	def books_closed(self, mess: str, posting_date) -> bool:
		billed_upto = frappe.db.get_value("Hostel Mess", mess, "billed_upto")
		return bool(billed_upto) and getdate(posting_date) <= getdate(billed_upto)

	def stock_exists(self, mess: str, entry_type: str, posting_date) -> bool:
		return bool(
			frappe.db.exists(
				"Mess Stock Entry",
				{"mess": mess, "entry_type": entry_type, "posting_date": posting_date, "docstatus": 1},
			)
		)

	def stock_receipt(self, mess: str, share: float, posting_date) -> None:
		# Looked up by date, not by a key: after a nightly reset the dates have moved.
		if self.stock_exists(mess, "Receipt", posting_date) or self.books_closed(mess, posting_date):
			return
		by_supplier: dict[int, list] = {}
		for item, qty, rate, supplier in STOCK_RECEIPT:
			if frappe.db.exists("Mess Item", item):
				by_supplier.setdefault(supplier, []).append(
					{"item": item, "qty": max(round(qty * share * self.scale), 1), "rate": rate}
				)
		for supplier, items in sorted(by_supplier.items()):
			doc = frappe.get_doc(
				{
					"doctype": "Mess Stock Entry",
					"entry_type": "Receipt",
					"mess": mess,
					"posting_date": posting_date,
					"posting_time": "08:00:00",
					"supplier": SUPPLIERS[supplier][0],
					"supplier_invoice_no": f"INV-{posting_date.strftime('%y%m')}-{supplier + 1:02d}",
					"supplier_invoice_date": posting_date,
					"items": items,
					"remarks": _("Demo receipt of monthly provisions"),
				}
			)
			doc.insert(ignore_permissions=True)
			doc.submit()
			self.bump("Mess Stock Entry")

	def stock_issue(self, mess: str, share: float, posting_date) -> None:
		if self.stock_exists(mess, "Issue", posting_date) or self.books_closed(mess, posting_date):
			return
		rng = self.draw("issue", mess, posting_date)
		items = []
		for item, qty, _rate, _supplier in STOCK_RECEIPT:
			uom = frappe.db.get_value("Mess Item", item, "uom")
			if not uom or item.startswith("LPG"):
				continue
			# Kitchens do not use exactly the same every day; a few per cent either way.
			issue = qty * share * self.scale * DAILY_ISSUE_SHARE * rng.uniform(0.92, 1.05)
			# Eggs and cylinders are issued in whole numbers; weights and volumes to 100 g / ml.
			issue = round(issue) if uom in COUNTED_UOMS else round(issue, 1)
			if issue > 0:
				items.append({"item": item, "qty": issue})
		if not items:
			return
		doc = frappe.get_doc(
			{
				"doctype": "Mess Stock Entry",
				"entry_type": "Issue",
				"mess": mess,
				"posting_date": posting_date,
				"posting_time": "06:00:00",
				"items": items,
				"remarks": _("Demo daily issue to the kitchen"),
			}
		)
		doc.insert(ignore_permissions=True)
		doc.submit()
		self.bump("Mess Stock Entry")

	def mess_expenses(self, mess: str, share: float, start, end, closed: bool) -> None:
		diners = max(round(self.count * share * 0.9), 1)
		cooks = max(2, round(diners / 30))
		rows = [
			("Electricity", 9, round(1800 + diners * 35, -2), _("Kitchen and dining hall electricity")),
			("Water", 14, round(800 + diners * 12, -2), _("Drinking water tanker and RO service")),
			("Maintenance", 19, 2500, _("Grinder and boiler servicing")),
		]
		if closed:
			# Wages are paid at the end of the month.
			rows.append(("Wages", (getdate(end) - getdate(start)).days, cooks * 9500, _("Cooks and helpers")))
		for expense_type, offset, amount, remarks in rows:
			posting_date = getdate(add_days(start, offset))
			if posting_date > getdate(end) or self.books_closed(mess, posting_date):
				continue
			if frappe.db.exists(
				"Mess Expense",
				{"mess": mess, "expense_type": expense_type, "posting_date": posting_date, "docstatus": 1},
			):
				continue
			doc = frappe.get_doc(
				{
					"doctype": "Mess Expense",
					"mess": mess,
					"posting_date": posting_date,
					"expense_type": expense_type,
					"amount": amount,
					"bill_no": f"{expense_type[:3].upper()}-{posting_date.strftime('%y%m')}",
					"remarks": remarks,
				}
			)
			doc.insert(ignore_permissions=True)
			doc.submit()
			self.bump("Mess Expense")

	def create_menus(self) -> None:
		"""Menus for last week, this week and next week, all published."""
		self.say(_("Weekly menus"))
		for name, _hostels, _share in MESSES:
			for weeks in (-1, 0, 1):
				self.safely(f"Menu {name} {weeks}", self.weekly_menu, name, self.monday(weeks))

	def weekly_menu(self, mess: str, monday=None) -> None:
		monday = getdate(monday) if monday else self.monday()
		if frappe.db.exists("Mess Menu", {"mess": mess, "week_start_date": monday}):
			return
		# Weeks take turns in where the menu starts, so consecutive weeks do not look identical.
		turn = monday.isocalendar()[1] % 7
		rows = []
		for day in range(7):
			date = monday + datetime.timedelta(days=day)
			for meal, dishes in MENU.items():
				rows.append(
					{
						"menu_date": date,
						"meal": meal,
						"dishes": dishes[(day + turn) % 7] if day < 6 else dishes[6],
						"is_special": int(day == 6 and meal in ("Lunch", "Dinner")),
					}
				)
		menu = frappe.get_doc(
			{
				"doctype": "Mess Menu",
				"mess": mess,
				"week_start_date": monday,
				"items": rows,
				"notes": _("Demo menu"),
			}
		)
		menu.insert(ignore_permissions=True)
		menu.publish()
		self.bump("Mess Menu")

	def create_reductions(self) -> None:
		"""Approved reductions in last month (they lower those students' mess bills) and a few
		waiting for approval next week."""
		self.say(_("Mess reductions"))
		residents = self.residents()
		last_month = self.month_start(1)
		for idx, student in enumerate(residents[3::9]):
			rng = self.draw("reduction", student.name, last_month)
			start = getdate(add_days(last_month, rng.randint(2, 20)))
			end = getdate(add_days(start, rng.randint(3, 6)))
			self.safely(
				f"Reduction {student.name}",
				self.reduction,
				student.name,
				start,
				end,
				"Approved",
				REDUCTION_REASONS[idx % len(REDUCTION_REASONS)],
			)
		for idx, student in enumerate(residents[5::23][:3]):
			start = getdate(add_days(self.today, 7 + idx))
			self.safely(
				f"Reduction {student.name} (pending)",
				self.reduction,
				student.name,
				start,
				getdate(add_days(start, 3)),
				"Pending",
				REDUCTION_REASONS[idx % len(REDUCTION_REASONS)],
			)

	def reduction(self, student: str, start, end, status: str, reason: tuple) -> None:
		overlapping = frappe.db.exists(
			"Mess Reduction Application",
			{
				"student": student,
				"status": ["in", ["Pending", "Approved"]],
				"from_date": ["<=", end],
				"to_date": [">=", start],
			},
		)
		if overlapping:
			return
		values = {
			"doctype": "Mess Reduction Application",
			"student": student,
			"from_date": start,
			"to_date": end,
			"reason": reason[0],
			"description": reason[1],
			"status": status,
			"applied_by": "Administrator",
		}
		if status == "Approved":
			values.update(
				approver="Administrator",
				decided_on=get_datetime(add_days(start, -2)).replace(hour=11),
				approval_remarks=_("Approved"),
			)
		frappe.get_doc(values).insert(ignore_permissions=True)
		self.bump("Mess Reduction Application")

	def create_bill_runs(self) -> None:
		"""Last month's mess bills, computed and published like the mess office does."""
		self.say(_("Mess bills for last month"))
		start = self.month_start(1)
		end = getdate(add_days(self.month_start(0), -1))
		for name, _hostels, _share in MESSES:
			self.isolated(f"Mess bill run {name} {start}", self.bill_run, name, start, end)

	def bill_run(self, mess: str, start, end) -> None:
		if frappe.db.exists("Mess Bill Run", {"mess": mess, "from_date": start, "docstatus": ["<", 2]}):
			return
		run = frappe.get_doc(
			{"doctype": "Mess Bill Run", "mess": mess, "from_date": start, "to_date": end}
		).insert(ignore_permissions=True)
		# Computed and published here and now, never handed to a background worker.
		run.flags.in_background_job = True
		run.compute_bills()
		run.reload()
		if not cint(run.bills_count):
			# Nobody ate at this mess last month (a very small data set): nothing to bill.
			frappe.delete_doc("Mess Bill Run", run.name, ignore_permissions=True, force=True)
			return
		run.flags.in_background_job = True
		run.flags.ignore_permissions = True
		run.submit()
		self.bump("Mess Bill Run")
		self.bump("Mess Bill", cint(run.bills_count))

	def create_indents(self) -> None:
		self.say(_("Mess indents"))
		for name, _hostels, share in MESSES:
			self.safely(f"Indent {name}", self.indent, name, share)

	def indent(self, mess: str, share: float) -> None:
		if frappe.db.exists("Mess Indent", {"mess": mess, "status": "Pending Approval"}):
			return
		items = []
		for item, qty, rate, _supplier in STOCK_RECEIPT[:6]:
			if frappe.db.exists("Mess Item", item):
				items.append(
					{
						"item": item,
						"required_qty": max(round(qty * share * self.scale / 2), 1),
						"estimated_rate": rate,
					}
				)
		if not items:
			return
		doc = frappe.get_doc(
			{
				"doctype": "Mess Indent",
				"mess": mess,
				"posting_date": self.today,
				"required_by": getdate(add_days(self.today, 3)),
				"items": items,
				"remarks": _("Provisions for the second half of the month"),
			}
		)
		doc.insert(ignore_permissions=True)
		doc.submit()
		self.bump("Mess Indent")

	# ---------------------------------------------------------------- services

	def create_services(self) -> None:
		self.say(_("Leaves, grievances and bonafide requests"))
		residents = self.residents()
		if not residents:
			return
		now = now_datetime()

		leave_count = max(3, len(residents) // 12)
		for idx in range(leave_count):
			student = residents[(idx * 7) % len(residents)]
			reason, destination = LEAVE_REASONS[idx % len(LEAVE_REASONS)]
			kind = ("Returned", "Approved", "Pending Warden Approval", "Pending Parent Approval")[idx % 4]
			source_id = f"{student.name}:{idx}"
			if self.demo_record("demo_leaves", source_id):
				continue
			if kind == "Returned":
				start = now - datetime.timedelta(days=20 + idx)
			else:
				start = now + datetime.timedelta(days=3 + idx)
			start = start.replace(hour=16, minute=0, second=0, microsecond=0)
			end = start + datetime.timedelta(days=2, hours=2)
			self.safely(
				f"Leave {source_id}",
				self.leave,
				source_id,
				student,
				kind,
				start,
				end,
				reason,
				destination,
			)

		self.todays_leaves(residents, now)
		self.create_grievances(residents, now)
		self.create_bonafide(residents)

	def leave(self, source_id, student, kind, start, end, reason, destination, **extra) -> str:
		guardian = frappe.db.get_value(
			"Hostel Student Guardian", {"parent": student.name, "is_primary": 1}, "guardian"
		)
		values = {
			"doctype": "Home Visit Leave",
			"student": student.name,
			"hostel": student.current_hostel,
			"room": student.current_room,
			"from_datetime": start,
			"to_datetime": end,
			"destination": destination,
			"reason": reason,
			"contact_during_leave": student.mobile_number,
			"applied_by": "Student",
			"parent_guardian": guardian,
			"status": kind,
			"parent_approval_status": "Pending" if kind == "Pending Parent Approval" else "Approved",
			"warden_approval_status": {
				"Pending Parent Approval": "Pending",
				"Pending Warden Approval": "Pending",
				"Rejected": "Rejected",
			}.get(kind, "Approved"),
		}
		if values["parent_approval_status"] == "Approved":
			values["parent_action_on"] = start - datetime.timedelta(days=1, hours=3)
		if kind in ("Returned", "Checked Out", "Overdue"):
			values["checked_out_on"] = start + datetime.timedelta(minutes=20)
		if kind == "Returned":
			values["returned_on"] = end - datetime.timedelta(hours=1)
		if kind == "Overdue":
			values["overdue_since"] = end + datetime.timedelta(hours=2)
		if kind not in ("Pending Parent Approval", "Pending Warden Approval"):
			values.update(warden_user="Administrator", warden_action_on=start - datetime.timedelta(hours=20))
		if kind == "Rejected":
			values["warden_remarks"] = _("Internal assessment on that day; please apply for the weekend.")
		if kind == "Cancelled":
			values["cancelled_by"] = "Administrator"
		values.update(extra)
		doc = frappe.get_doc(values).insert(ignore_permissions=True)
		self.remember("demo_leaves", source_id, "Home Visit Leave", doc.name)
		self.bump("Home Visit Leave")
		return doc.name

	def todays_leaves(self, residents: list, now) -> None:
		"""Leaves in every state, dated around today: two waiting for approval, one due to leave
		at the gate, students away now (one of them late back), one rejected and one cancelled."""
		# kind, starts (hours from now), lasts (hours), reason
		plan = (
			("Pending Parent Approval", 3, 50, LEAVE_REASONS[2]),
			("Pending Warden Approval", 4, 52, LEAVE_REASONS[0]),
			("Approved", 1, 30, LEAVE_REASONS[1]),
			("Checked Out", -26, 72, LEAVE_REASONS[2]),
			("Overdue", -98, 72, LEAVE_REASONS[1]),
			("Rejected", -140, 48, LEAVE_REASONS[4]),
			("Cancelled", 120, 48, LEAVE_REASONS[3]),
		)
		busy = set(
			frappe.get_all(
				"Home Visit Leave",
				filters={"status": ["not in", ["Cancelled", "Rejected", "Returned"]]},
				pluck="student",
			)
		)
		for idx, (kind, starts_in, lasts, (reason, destination)) in enumerate(plan):
			source_id = f"today:{kind}"
			if self.demo_record("demo_leaves", source_id):
				continue
			student = next(
				(
					residents[(idx * 13 + 5 + step) % len(residents)]
					for step in range(len(residents))
					if residents[(idx * 13 + 5 + step) % len(residents)].name not in busy
				),
				None,
			)
			if not student:
				return
			start = now + datetime.timedelta(hours=starts_in)
			if starts_in > 0 and start.date() != now.date() and kind != "Cancelled":
				# Late in the evening: still today, so it shows among today's departures.
				start = now + datetime.timedelta(minutes=30)
			start = start.replace(second=0, microsecond=0)
			busy.add(student.name)
			self.safely(
				f"Leave {source_id}",
				self.leave,
				source_id,
				student,
				kind,
				start,
				start + datetime.timedelta(hours=lasts),
				reason,
				destination,
			)

	def create_grievances(self, residents: list, now) -> None:
		for idx, (category, subject, description, priority, status, resolution) in enumerate(GRIEVANCES):
			student = residents[(idx * 5 + 2) % len(residents)]
			source_id = f"{idx}"
			if self.demo_record("demo_grievances", source_id):
				continue
			values = {
				"doctype": "Hostel Grievance",
				"student": student.name,
				"hostel": student.current_hostel,
				"room": student.current_room,
				"category": category,
				"subject": subject,
				"description": description,
				"priority": priority,
				"status": status,
			}
			if resolution:
				values.update(
					resolution=resolution,
					resolved_by="Administrator",
					resolved_on=now - datetime.timedelta(days=idx + 1),
				)
			if status == "Escalated":
				values.update(
					escalation_level=1,
					escalated_on=now - datetime.timedelta(hours=20),
					sla_due_by=now - datetime.timedelta(hours=22),
					sla_breached=1,
				)
			if status == "Reopened":
				values["reopen_count"] = 1

			def grievance(values=values, source_id=source_id):
				doc = frappe.get_doc(values).insert(ignore_permissions=True)
				self.remember("demo_grievances", source_id, "Hostel Grievance", doc.name)
				self.bump("Hostel Grievance")

			self.safely(f"Grievance {source_id}", grievance)

	def create_bonafide(self, residents: list) -> None:
		# purpose, approve, hand over
		plan = (
			("Bank Loan", True, False),
			("Scholarship", False, False),
			("Passport", False, False),
			("Internship", True, True),
			("Address Proof", True, True),
		)
		for idx, (purpose, approve, issue) in enumerate(plan):
			student = residents[(idx * 11 + 4) % len(residents)]
			source_id = f"{idx}"
			if self.demo_record("demo_bonafide", source_id):
				continue

			def bonafide(student=student, purpose=purpose, source_id=source_id, approve=approve, issue=issue):
				doc = frappe.get_doc(
					{
						"doctype": "Bonafide Certificate Request",
						"student": student.name,
						"purpose": purpose,
						"purpose_details": _("Education loan application")
						if purpose == "Bank Loan"
						else _("Summer internship at a Chennai firm")
						if purpose == "Internship"
						else None,
						"status": "Pending",
					}
				).insert(ignore_permissions=True)
				if approve:
					doc.approve_by("Administrator")
				if issue:
					doc.mark_issued_by("Administrator")
				self.remember("demo_bonafide", source_id, "Bonafide Certificate Request", doc.name)
				self.bump("Bonafide Certificate Request")

			self.safely(f"Bonafide {source_id}", bonafide)

	def create_feedback(self) -> None:
		"""Monthly feedback from about a third of the residents, last month and this month."""
		self.say(_("Feedback"))
		from hostel360.student_services.doctype.hostel_feedback.hostel_feedback import has_submitted
		from hostel360.student_services.services import submit_feedback

		residents = self.residents()
		for months_back in (1, 0):
			period = self.month_start(months_back).strftime("%Y-%m")
			for idx, student in enumerate(residents[1::3]):
				rng = self.draw("feedback", student.name, period)
				feedback_type = "Mess" if idx % 3 else "Hostel"
				if has_submitted(student.name, feedback_type, period):
					continue
				ratings = {
					field: max(1, min(5, round(rng.gauss(mean, 0.9))))
					for field, mean in (
						("overall", 3.9),
						("food_quality", 3.5),
						("cleanliness", 3.8),
						("staff_behaviour", 4.2),
						("facilities", 3.6),
					)
				}
				data = {
					"feedback_type": feedback_type,
					"period": period,
					"comments": rng.choice(FEEDBACK_COMMENTS),
					"is_anonymous": int(rng.random() < 0.2),
					**ratings,
				}
				if self.safely(f"Feedback {student.name} {period}", submit_feedback, student.name, data):
					self.bump("Hostel Feedback")

	# ---------------------------------------------------------------- operations

	def create_operations(self) -> None:
		"""What HyFib operations and the institution's auditors look at: tickets, alert
		history, monitoring samples, backups and last month's performance report."""
		self.say(_("Support tickets, alerts, monitoring history and backups"))
		self.alert_history()
		self.support_tickets()
		self.isolated("Monitoring samples", self.metric_samples)
		self.isolated("Backup history", self.backup_history)
		self.isolated("Monthly performance report", self.monthly_report)

	def alert_history(self) -> None:
		now = now_datetime()
		for idx, (
			key,
			title,
			severity,
			category,
			source,
			days_ago,
			minutes,
			count,
			outage,
			text,
		) in enumerate(ALERT_HISTORY):
			source_id = f"{idx}"
			if self.demo_record("demo_alerts", source_id):
				continue
			first_seen = self.alert_start(now, days_ago, idx)

			def alert(
				key=key,
				title=title,
				severity=severity,
				category=category,
				source=source,
				minutes=minutes,
				count=count,
				outage=outage,
				text=text,
				first_seen=first_seen,
				source_id=source_id,
			):
				resolved_on = first_seen + datetime.timedelta(minutes=minutes)
				doc = frappe.get_doc(
					{
						"doctype": "Ops Alert",
						"alert_key": key,
						"title": title,
						"severity": severity,
						"category": category,
						"source": source,
						"status": "Resolved",
						"description": text,
						"first_seen": first_seen,
						"last_seen": resolved_on - datetime.timedelta(minutes=1),
						"occurrence_count": count,
						"is_outage": outage,
						"acknowledged_by": "Administrator" if severity == "Critical" else None,
						"acknowledged_on": first_seen + datetime.timedelta(minutes=4)
						if severity == "Critical"
						else None,
						"resolved_on": resolved_on,
						"resolved_by": "Administrator",
						"resolved_via": source if source in ("Health Check", "Host Agent") else "App",
						"resolution_notes": _("Recovered."),
						"escalation_level": 1,
						"notified_levels": "1",
					}
				)
				# History: nobody is paged for alerts that were resolved long ago.
				doc.flags.raised_by_service = True
				doc.insert(ignore_permissions=True)
				self.remember("demo_alerts", source_id, "Ops Alert", doc.name)
				self.bump("Ops Alert")

			self.safely(f"Alert {key}", alert)

	@staticmethod
	def alert_start(now, days_ago: int, idx: int):
		return (now - datetime.timedelta(days=days_ago)).replace(
			hour=(9 + idx * 3) % 24, minute=(idx * 17) % 60, second=0, microsecond=0
		)

	def support_tickets(self) -> None:
		from hostel360.hostel_operations import alerts

		now = now_datetime()
		for idx, (subject, category, priority, status, hours_ago, description, resolution) in enumerate(
			TICKETS
		):
			source_id = f"{idx}"
			if self.demo_record("demo_tickets", source_id):
				continue

			def ticket(
				subject=subject,
				category=category,
				priority=priority,
				status=status,
				hours_ago=hours_ago,
				description=description,
				resolution=resolution,
				source_id=source_id,
			):
				raised = (now - datetime.timedelta(hours=hours_ago)).replace(second=0, microsecond=0)
				doc = frappe.get_doc(
					{
						"doctype": "Support Ticket",
						"subject": subject,
						"category": category,
						"priority": priority,
						"status": status,
						"raised_by_name": _("Hostel Office"),
						"contact_phone": "9000000801",
						"description": description,
						"resolution_details": resolution,
					}
				)
				doc.insert(ignore_permissions=True)
				# Back-date the ticket and its SLA clock to when it was raised.
				closed = status in ("Resolved", "Closed")
				values = {
					"creation": raised,
					"response_due_by": raised + datetime.timedelta(hours=1),
					"resolution_due_by": raised + datetime.timedelta(hours=4),
				}
				if status != "Open":
					values["first_response_on"] = raised + datetime.timedelta(minutes=18 + 9 * int(source_id))
				if closed:
					values["resolved_on"] = raised + datetime.timedelta(
						hours=2, minutes=10 + 7 * int(source_id)
					)
				if status == "Waiting on Institution":
					values["waiting_since"] = raised + datetime.timedelta(hours=1)
				frappe.db.set_value("Support Ticket", doc.name, values, update_modified=False)
				if doc.ops_alert:
					alert_values = {"first_seen": raised, "last_seen": raised}
					frappe.db.set_value("Ops Alert", doc.ops_alert, alert_values, update_modified=False)
					if closed:
						alerts.resolve_alert(
							f"support.ticket.{doc.name}",
							notes=_("Ticket {0}.").format(_(status)),
							source="Support",
						)
				self.remember("demo_tickets", source_id, "Support Ticket", doc.name)
				self.bump("Support Ticket")

			self.safely(f"Ticket {source_id}", ticket)

	def metric_samples(self) -> None:
		"""Monitoring history since the start of last month: the uptime probe every minute
		(with the outage in the alert history), server load every quarter of an hour."""
		now = now_datetime().replace(second=0, microsecond=0)
		start = get_datetime(self.month_start(1))
		outage = ALERT_HISTORY[0]
		outage_start = self.alert_start(now_datetime(), outage[5], 0)
		outage_end = outage_start + datetime.timedelta(minutes=outage[6])
		day = start
		while day < now:
			day_end = min(day + datetime.timedelta(days=1), now)
			if not frappe.db.exists(
				"Ops Metric Sample", {"metric": "uptime_probe", "recorded_at": ["between", [day, day_end]]}
			):
				self.metric_day(day, day_end, outage_start, outage_end)
			day += datetime.timedelta(days=1)

	def metric_day(self, day, day_end, outage_start, outage_end) -> None:
		from uuid import uuid7

		rng = self.draw("metrics", day.date())
		stamp = now_datetime()
		rows = []

		def add(metric, value, unit, at, source="Host Agent"):
			rows.append(
				(
					str(uuid7()),
					metric,
					value,
					unit,
					source,
					DEMO_HOST,
					at,
					stamp,
					stamp,
					"Administrator",
					"Administrator",
				)
			)

		minute = day
		while minute < day_end:
			up = not (outage_start <= minute < outage_end)
			add("uptime_probe", 1 if up else 0, "", minute, source="Uptime Probe")
			if minute.minute % 15 == 0:
				hour = minute.hour + minute.minute / 60
				# Busy in the morning (fees, leave at 08:00) and in the evening (menu, grievances).
				load = 18 + 22 * max(0.0, 1 - abs(hour - 9) / 3) + 26 * max(0.0, 1 - abs(hour - 20) / 3)
				add("cpu_percent", round(load + rng.uniform(-4, 6), 1), "%", minute)
				add("memory_percent", round(58 + load / 4 + rng.uniform(-2, 2), 1), "%", minute)
				add("disk_percent", round(41 + (minute - day).total_seconds() / 86400 * 0.05, 2), "%", minute)
				add("db_latency_ms", round(4 + load / 9 + rng.uniform(0, 3), 1), "ms", minute, "Health Check")
			if minute.minute == 0:
				add(
					"waf_blocks", rng.randint(0, 6) + (14 if minute.hour in (2, 3) else 0), "requests", minute
				)
			minute += datetime.timedelta(minutes=1)
		for chunk in range(0, len(rows), 5000):
			frappe.db.bulk_insert(
				"Ops Metric Sample",
				fields=[
					"name",
					"metric",
					"value",
					"unit",
					"source",
					"host",
					"recorded_at",
					"creation",
					"modified",
					"owner",
					"modified_by",
				],
				values=rows[chunk : chunk + 5000],
			)
		self.bump("Ops Metric Sample", len(rows))

	def backup_history(self) -> None:
		"""Nightly backups for the last month, weekly ones on Sundays and a restore test on the
		first of each month; one nightly backup failed (see the alert history)."""
		from hostel360.hostel_operations.doctype.ops_backup_record.ops_backup_record import human_size

		failed_day = getdate(add_days(self.today, -ALERT_HISTORY[4][5]))
		for days_ago in range(30, 0, -1):
			date = getdate(add_days(self.today, -days_ago))
			rng = self.draw("backup", date)
			plan = [("Daily", 2, 0.92)]
			if date.weekday() == 6:
				plan.append(("Weekly", 3, 0.95))
			if date.day == 1:
				plan.append(("Restore Test", 4, 0.0))
			for backup_type, hour, ratio in plan:
				started = get_datetime(date).replace(hour=hour, minute=15)
				if frappe.db.exists(
					"Ops Backup Record",
					{
						"backup_type": backup_type,
						"started_at": ["between", [get_datetime(date), get_datetime(add_days(date, 1))]],
					},
				):
					continue
				failed = backup_type == "Daily" and date == failed_day
				size = (
					0
					if backup_type == "Restore Test"
					else int((1.15 + 0.004 * (30 - days_ago)) * 1024**3 * ratio)
				)
				finished = started + datetime.timedelta(seconds=rng.randint(240, 420) if not failed else 95)
				doc = frappe.get_doc(
					{
						"doctype": "Ops Backup Record",
						"backup_type": backup_type,
						"status": "Failed" if failed else "Success",
						"host": DEMO_HOST,
						"started_at": started,
						"finished_at": finished,
						"duration_seconds": int((finished - started).total_seconds()),
						"size_bytes": size,
						"size_display": human_size(size) if size else "",
						"snapshot_id": "" if failed else f"{rng.getrandbits(32):08x}",
						"repository": "restic: oci-mumbai/hostel360",
						"source": "h360-backup",
						"message": _(
							"Object storage did not answer within 90 seconds; the next run succeeded."
						)
						if failed
						else (
							_("Restored to a scratch database and checked.")
							if backup_type == "Restore Test"
							else ""
						),
					}
				)
				# Written as history: the alert a failure raises is already in the alert history.
				doc.set_new_name()
				doc.creation = doc.modified = finished
				doc.owner = doc.modified_by = "Administrator"
				doc.db_insert()
				self.bump("Ops Backup Record")

	def refresh_report(self) -> None:
		self.isolated("Monthly performance report", self.monthly_report)

	def monthly_report(self) -> None:
		from hostel360.hostel_operations.reports import generate_monthly_performance_report, previous_month

		month = previous_month()
		if frappe.db.exists("Monthly Performance Report", {"month": month, "docstatus": 1}):
			return
		generate_monthly_performance_report(month, send=False)
		self.bump("Monthly Performance Report")


def generate(students: int = 60, seed: int = 2026, progress=None) -> dict:
	"""Create (or top up) the demo data set. Returns counts of what was created."""
	return DemoDataBuilder(cint(students), cint(seed), progress=progress).run()


def refresh_calendar(seed: int = 2026, progress=None) -> dict:
	"""Records tied to the calendar rather than to "now": this week's and next week's menus,
	last month's mess bills and performance report, this month's feedback.

	Used after a demonstration site is restored and its dates moved to today (see
	``hostel360.demo.redate``): everything else keeps its place relative to now, but a menu
	is for a particular week and a bill for a particular month.
	"""
	builder = DemoDataBuilder(1, cint(seed), progress=progress)
	builder.count = max(frappe.db.count("Hostel Student", {"register_number": ["like", "UAT%"]}), 1)
	builder.scale = max(1.0, builder.count / 60)
	return builder.run(
		steps=(
			builder.create_menus,
			builder.create_bill_runs,
			builder.create_feedback,
			builder.refresh_report,
		)
	)
