"""Demonstration and UAT data for a Tamil Nadu engineering college hostel.

``generate`` builds a realistic, self-consistent data set: two boys' hostels and one
girls' hostel with blocks, floors, rooms and beds; students with guardians and Tamil
names from the southern districts; allotments; this year's fee structure with invoices
and a mix of paid, part-paid and unpaid dues; mess items, stock, a published weekly menu;
and a few leaves, grievances and bonafide requests.

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
from frappe.utils import add_days, cint, flt, get_datetime, getdate, now_datetime, nowdate

from hostel360.hostel_migration.doctype.migration_record_map.migration_record_map import make_map_key
from hostel360.hostel_migration.engine import MAP_DOCTYPE

DEMO_DOMAIN = "demo.hostel360.invalid"
# Migration Record Map namespace for records made here.
DEMO_SOURCE_SYSTEM = "hostel360-demo"
CAMPUS_ADDRESS = "ACGCET Campus, Karaikudi – 630 003, Sivagangai District, Tamil Nadu"

DEPARTMENTS = (
	("Civil Engineering", "CE"),
	("Mechanical Engineering", "ME"),
	("Electrical and Electronics Engineering", "EE"),
	("Electronics and Communication Engineering", "EC"),
	("Computer Science and Engineering", "CS"),
)
PROGRAMS = tuple((f"B.E. {name}", name, "UG", 4) for name, _code in DEPARTMENTS) + (
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
	"Arun", "Karthik", "Vignesh", "Surya", "Prakash", "Dinesh", "Saravanan", "Manikandan", "Ramesh",
	"Gokul", "Harish", "Balaji", "Senthil", "Muthu", "Aravind", "Ajith", "Vijay", "Naveen", "Santhosh",
	"Sathish", "Bharath", "Kishore", "Lokesh", "Madhan", "Pradeep", "Ragul", "Sakthi", "Tamilselvan",
	"Vasanth", "Yuvaraj", "Ezhil", "Ilango", "Jeeva", "Kathir", "Nithish", "Pandi", "Selvam", "Hariharan",
)
FEMALE_NAMES = (
	"Priya", "Divya", "Kavya", "Lakshmi", "Meena", "Anitha", "Deepa", "Revathi", "Sangeetha", "Keerthana",
	"Nandhini", "Swathi", "Abinaya", "Gayathri", "Harini", "Janani", "Kaviya", "Monisha", "Pavithra",
	"Ramya", "Sowmiya", "Thenmozhi", "Vaishnavi", "Yamini", "Aishwarya", "Bhavani", "Dharani", "Ilakkiya",
)
FATHER_NAMES = (
	"Murugan", "Kumar", "Selvaraj", "Pandian", "Subramanian", "Krishnan", "Natarajan", "Ramasamy",
	"Velu", "Ganesan", "Shanmugam", "Rajendran", "Palanisamy", "Arumugam", "Chinnasamy", "Kannan",
	"Sundaram", "Mohan", "Periyasamy", "Raman", "Thangaraj", "Veerappan", "Karuppiah", "Sekar",
)
MOTHER_NAMES = (
	"Selvi", "Lakshmi", "Valli", "Meenakshi", "Kamala", "Saraswathi", "Vijaya", "Chitra", "Malathi",
	"Rani", "Jothi", "Padma", "Shanthi", "Bhuvana", "Kalaivani", "Amudha",
)
OCCUPATIONS = (
	"Farmer", "Teacher", "Government Employee", "Business", "Driver", "Shop Owner", "Electrician",
	"Bank Employee", "Contractor", "Private Employee", "Weaver", "Fisherman",
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
	"Gandhi Street", "Nehru Nagar", "Anna Salai", "Kamarajar Street", "Bharathi Nagar", "Main Road",
	"Periyar Nagar", "Vivekananda Street", "Thiruvalluvar Street", "Mariamman Kovil Street",
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
	("Plumbing", "Tap leaking in second floor bathroom", "The tap near the wash basin has been leaking for three days and water collects on the floor.", "Medium", "Resolved", "Washer replaced by the plumber."),
	("Electrical", "Ceiling fan not working", "The fan in our room stopped working last night. The regulator is also loose.", "High", "In Progress", None),
	("Internet", "Wi-Fi very slow in B block", "Wi-Fi in B block is too slow to download lecture notes in the evening.", "Medium", "Open", None),
	("Mess and Food", "Sambar too watery at dinner", "Dinner sambar has been very thin this week. Please check the quantity of dal used.", "Low", "Resolved", "Discussed with the mess committee; dal quantity corrected."),
	("Water", "No drinking water on ground floor", "The RO water point on the ground floor has not been working since Monday.", "High", "Open", None),
	("Cleanliness", "Corridor not cleaned", "The first floor corridor was not cleaned on Saturday and Sunday.", "Low", "Closed", "Housekeeping roster revised."),
)
LEAVE_REASONS = (
	("Pongal holidays with family", "Home"),
	("Sister's marriage", "Home"),
	("Temple festival in native village", "Home"),
	("Medical check-up", "Madurai"),
	("Inter-college sports meet", "Tiruchirappalli"),
)


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
			number = str(self.rng.choice((6, 7, 8, 9))) + "".join(str(self.rng.randrange(10)) for _i in range(9))
			if number not in self.used_mobiles:
				self.used_mobiles.add(number)
				return number

	# ---------------------------------------------------------------- run

	def check_messaging(self) -> None:
		if not frappe.db.exists("DocType", "Messaging Settings"):
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

	def run(self) -> dict:
		self.check_messaging()
		previous = (frappe.flags.hostel360_migration, frappe.flags.mute_emails, frappe.session.user)
		frappe.flags.hostel360_migration = True
		frappe.flags.mute_emails = True
		frappe.set_user("Administrator")
		try:
			for step in (
				self.setup_masters,
				self.setup_hostels,
				self.create_students,
				self.allot_rooms,
				self.create_fees,
				self.setup_mess,
				self.create_services,
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
			frappe.db.get_single_value("Hostel Settings", "current_academic_year") or ensure_current_academic_year()
		)
		self.year_start = getdate(frappe.db.get_value("Hostel Academic Year", self.academic_year, "start_date"))

		for name, code in DEPARTMENTS:
			if not frappe.db.exists("Academic Department", name):
				frappe.get_doc({"doctype": "Academic Department", "department_name": name, "department_code": code}).insert(
					ignore_permissions=True
				)
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
				"address": f"{person.address}, {person.city}, {person.district} District – {person.pincode}",
				"validation_status": "Validated",
			}
		).insert(ignore_permissions=True)
		self.bump("Hostel Guardian")
		return doc.name

	def create_student(self, person) -> None:
		guardians = [
			{
				"guardian": self.create_guardian(person.father, "Father", person.father_mobile, person.occupation, person),
				"relation": "Father",
				"is_primary": 1,
				"can_approve_leave": 1,
			}
		]
		if person.mother_mobile:
			guardians.append(
				{
					"guardian": self.create_guardian(person.mother, "Mother", person.mother_mobile, "Homemaker", person),
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
			# Fill the emptiest hostel first so both boys' hostels are in use.
			hostel = max(hostels, key=lambda h: (frappe.db.count("Hostel Bed", {**vacant, "hostel": h}), h))
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
				"Hostel Fee Invoice", {"student": student.name, "fee_structure": structure, "docstatus": ["<", 2]}
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

	# ---------------------------------------------------------------- mess

	def setup_mess(self) -> None:
		self.say(_("Mess, suppliers, stock and menu"))
		messes = (("Boys Mess", ("BH1", "BH2"), 0.65), ("Girls Mess", ("GH1",), 0.35))
		for name, hostels, _share in messes:
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

		month_start = getdate(nowdate()).replace(day=1)
		for name, _hostels, share in messes:
			self.safely(f"Stock receipt {name}", self.stock_receipt, name, share, month_start)
			for offset in range(1, 6):
				day = add_days(nowdate(), -offset)
				if getdate(day) < month_start:
					break
				self.safely(f"Stock issue {name} {day}", self.stock_issue, name, share, day)
			self.safely(f"Menu {name}", self.weekly_menu, name)

	def stock_receipt(self, mess: str, share: float, posting_date) -> None:
		key = f"{mess}:receipt:{posting_date}"
		if self.demo_record("demo_stock", key):
			return
		by_supplier: dict[int, list] = {}
		for item, qty, rate, supplier in STOCK_RECEIPT:
			if frappe.db.exists("Mess Item", item):
				by_supplier.setdefault(supplier, []).append({"item": item, "qty": max(round(qty * share), 1), "rate": rate})
		names = []
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
			names.append(doc.name)
			self.bump("Mess Stock Entry")
		if names:
			self.remember("demo_stock", key, "Mess Stock Entry", names[0])

	def stock_issue(self, mess: str, share: float, posting_date) -> None:
		key = f"{mess}:issue:{posting_date}"
		if self.demo_record("demo_stock", key):
			return
		items = []
		for item, qty, _rate, _supplier in STOCK_RECEIPT:
			uom = frappe.db.get_value("Mess Item", item, "uom")
			if not uom or item.startswith("LPG"):
				continue
			issue = qty * share * DAILY_ISSUE_SHARE
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
		self.remember("demo_stock", key, "Mess Stock Entry", doc.name)
		self.bump("Mess Stock Entry")

	def weekly_menu(self, mess: str) -> None:
		today = getdate(nowdate())
		monday = today - datetime.timedelta(days=today.weekday())
		if frappe.db.exists("Mess Menu", {"mess": mess, "week_start_date": monday}):
			return
		rows = []
		for day in range(7):
			date = monday + datetime.timedelta(days=day)
			for meal, dishes in MENU.items():
				rows.append({"menu_date": date, "meal": meal, "dishes": dishes[day], "is_special": int(day == 6 and meal in ("Lunch", "Dinner"))})
		menu = frappe.get_doc(
			{"doctype": "Mess Menu", "mess": mess, "week_start_date": monday, "items": rows, "notes": _("Demo menu")}
		)
		menu.insert(ignore_permissions=True)
		menu.publish()
		self.bump("Mess Menu")

	# ---------------------------------------------------------------- services

	def create_services(self) -> None:
		self.say(_("Leaves, grievances and bonafide requests"))
		residents = frappe.get_all(
			"Hostel Student",
			filters={"register_number": ["like", "UAT%"], "current_allotment": ["is", "set"]},
			fields=["name", "current_hostel", "current_room", "mobile_number"],
			order_by="name asc",
			limit_page_length=0,
		)
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
				"warden_approval_status": "Approved" if kind in ("Returned", "Approved") else "Pending",
			}
			if kind == "Returned":
				values.update(checked_out_on=start, returned_on=end - datetime.timedelta(hours=1))
			if kind in ("Returned", "Approved"):
				values.update(warden_user="Administrator", warden_action_on=start - datetime.timedelta(days=1))

			def leave(values=values, source_id=source_id):
				doc = frappe.get_doc(values).insert(ignore_permissions=True)
				self.remember("demo_leaves", source_id, "Home Visit Leave", doc.name)
				self.bump("Home Visit Leave")

			self.safely(f"Leave {source_id}", leave)

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

			def grievance(values=values, source_id=source_id):
				doc = frappe.get_doc(values).insert(ignore_permissions=True)
				self.remember("demo_grievances", source_id, "Hostel Grievance", doc.name)
				self.bump("Hostel Grievance")

			self.safely(f"Grievance {source_id}", grievance)

		for idx, purpose in enumerate(("Bank Loan", "Scholarship", "Passport")):
			student = residents[(idx * 11 + 4) % len(residents)]
			source_id = f"{idx}"
			if self.demo_record("demo_bonafide", source_id):
				continue

			def bonafide(student=student, purpose=purpose, source_id=source_id, approve=idx == 0):
				doc = frappe.get_doc(
					{
						"doctype": "Bonafide Certificate Request",
						"student": student.name,
						"purpose": purpose,
						"purpose_details": _("Education loan application") if purpose == "Bank Loan" else None,
						"status": "Pending",
					}
				).insert(ignore_permissions=True)
				if approve:
					doc.approve_by("Administrator")
				self.remember("demo_bonafide", source_id, "Bonafide Certificate Request", doc.name)
				self.bump("Bonafide Certificate Request")

			self.safely(f"Bonafide {source_id}", bonafide)


def generate(students: int = 60, seed: int = 2026, progress=None) -> dict:
	"""Create (or top up) the demo data set. Returns counts of what was created."""
	return DemoDataBuilder(cint(students), cint(seed), progress=progress).run()
