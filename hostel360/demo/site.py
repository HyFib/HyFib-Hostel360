"""Preparing and resetting a demonstration site (``h360-demo-site``, ``h360-demo-reset``).

``prepare_demo_site`` turns a new site into the public demonstration of Hostel360 for the
ACGCET hostels: it switches demonstration mode on before anything else (so nothing can be
sent from then on), runs the ordinary site setup, fills the site with the demonstration
data set, creates one desk account per hostel role and three students and two parents for
the portal, makes the site safe to open to anyone, and finally takes the golden snapshot
that every reset goes back to. It refuses a site that holds anything but demonstration
data, and running it again is safe: it first puts the site back to its snapshot, then tops
up whatever is missing, rotates the shared password and takes a new snapshot.

``reset_demo_site`` restores the snapshot, moves its dates to today (``redate``), adds what
belongs to the current week and month, and signs everybody out. It is meant to run every
night.
"""

import datetime
import secrets
import time

import frappe
from frappe import _
from frappe.utils import cint, getdate, now_datetime, nowdate

from hostel360.demo import (
	ACCOUNTS_KEY,
	CONFIG_KEYS,
	DEMO_MODE_KEY,
	OTP_KEY,
	PASSWORD_KEY,
	USERS_KEY,
	demo_otp,
	demo_password,
	is_demo_site,
)
from hostel360.demo.accounts import SETUP_FLAG
from hostel360.setup.roles import (
	ADMIN_ROLE,
	AUDITOR_ROLE,
	DEPUTY_WARDEN_ROLE,
	MESS_MANAGER_ROLE,
	OFFICE_ROLE,
	OPERATIONS_ROLE,
	SECURITY_ROLE,
	WARDEN_ROLE,
)

INSTITUTION_NAME = "Alagappa Chettiar Government College of Engineering and Technology"
SHORT_NAME = "ACGCET"
INSTITUTION_ADDRESS = "Karaikudi 630 003, Sivagangai District, Tamil Nadu"
# Staff logins live on the reserved .invalid domain: nothing could ever be delivered there.
STAFF_DOMAIN = "demo.invalid"
DEFAULT_STUDENTS = 240
DEFAULT_SEED = 2026
# Shared demo accounts are used by many visitors at once.
DEMO_SIMULTANEOUS_SESSIONS = 500
LOCK_NAME = "hostel360_demo"
LOCK_TIMEOUT_SECONDS = 600
# Site config is cached for 60 seconds in every web worker (frappe.config).
MAINTENANCE_GRACE_SECONDS = 65

# login, name, role, Hostel Staff designation (None: no staff record), hostels, looks after
STAFF = (
	("admin", "Revathi Sundaram", ADMIN_ROLE, "Hostel Administrator", (), "All hostels and settings"),
	("warden", "Dr. K. Senthilkumar", WARDEN_ROLE, "Warden", (), "Chief warden, all hostels"),
	("deputy.warden", "M. Prabhakaran", DEPUTY_WARDEN_ROLE, "Deputy Warden", ("BH2",), "Boys Hostel II"),
	(
		"office",
		"S. Kalaiselvi",
		OFFICE_ROLE,
		"Office Staff",
		("BH1", "BH2", "GH1"),
		"Registrations and fees, all hostels",
	),
	("mess", "R. Murugesan", MESS_MANAGER_ROLE, "Mess Manager", (), "Boys Mess and Girls Mess"),
	("security", "P. Ganesan", SECURITY_ROLE, "Security", ("BH1", "BH2", "GH1"), "Main gate, all hostels"),
	("auditor", "Dr. N. Meenakshi", AUDITOR_ROLE, None, (), "Read-only: reports, dashboards, monitoring"),
	(
		"operations",
		"HyFib Operations Desk",
		OPERATIONS_ROLE,
		None,
		(),
		"Alerts, health checks, support tickets",
	),
)
ADMIN_LOGIN = f"admin@{STAFF_DOMAIN}"
# Fictitious, easy to type numbers for the published portal accounts.
PORTAL_MOBILES = {
	"student_dues": "9000000001",
	"student_leave": "9000000002",
	"student_girls": "9000000003",
	"parent_leave": "9000000011",
	"parent_two_wards": "9000000012",
}
STAFF_MOBILE_PREFIX = "90000007"
PASSWORD_WORDS = (
	"Banyan",
	"Lotus",
	"Mango",
	"Peacock",
	"Jasmine",
	"Monsoon",
	"Kolam",
	"Veena",
	"Harbour",
	"Palmyra",
	"Tamarind",
	"Lantern",
)


class DemoSiteError(frappe.ValidationError):
	pass


# --------------------------------------------------------------------------- config


def set_config(values: dict) -> None:
	"""Write keys to site_config.json (None removes one) and to this process's config."""
	from frappe.installer import update_site_config

	for key, value in values.items():
		if value is None:
			update_site_config(key, "None")
			frappe.local.conf.pop(key, None)
		else:
			update_site_config(key, value)


def current_config() -> dict:
	return {key: frappe.local.conf.get(key) for key in CONFIG_KEYS if frappe.local.conf.get(key) is not None}


class setup_mode:
	"""Lets the demo command change the locked demo accounts (see ``demo.accounts``)."""

	def __enter__(self):
		self.previous = frappe.flags.get(SETUP_FLAG)
		frappe.flags[SETUP_FLAG] = True
		return self

	def __exit__(self, *exc):
		frappe.flags[SETUP_FLAG] = self.previous


def demo_lock():
	from frappe.utils.synchronization import filelock

	return filelock(LOCK_NAME, timeout=LOCK_TIMEOUT_SECONDS)


# --------------------------------------------------------------------------- prepare


def prepare_demo_site(
	students: int = DEFAULT_STUDENTS,
	seed: int = DEFAULT_SEED,
	portal_url: str | None = None,
	keep_password: bool = False,
	progress=None,
) -> dict:
	"""Prepare (or refresh) the demonstration site. See the module docstring."""
	from hostel360.demo.snapshot import read_manifest

	say = progress or (lambda message: None)
	if is_demo_site() and read_manifest():
		say(_("Putting the site back to its last snapshot first"))
		reset_demo_site(progress=progress)
	check_only_demo_data()

	with demo_lock():
		# Demonstration mode first: from this moment no message, alert or payment can leave.
		set_config({DEMO_MODE_KEY: 1, "mute_emails": 1, OTP_KEY: demo_otp()})
		previous_password = demo_password()
		with setup_mode():
			say(_("Site setup"))
			run_site_setup(portal_url or f"https://{frappe.local.site}")
			apply_safe_settings()
			frappe.db.commit()  # nosemgrep

			say(_("Demonstration data"))
			from hostel360.hostel_migration.demo_data import generate

			data = generate(students=students, seed=seed, progress=progress)

			say(_("Demonstration accounts"))
			password = previous_password if keep_password and previous_password else new_password()
			staff = ensure_staff_accounts(password)
			portal = ensure_portal_accounts()
			assign_work({row["role"]: row["user"] for row in staff})
			frappe.db.commit()  # nosemgrep

		accounts = staff + portal
		set_config(
			{
				PASSWORD_KEY: password,
				USERS_KEY: [row["user"] for row in accounts],
				ACCOUNTS_KEY: accounts,
			}
		)
		sign_everyone_out()
		frappe.clear_cache()

		say(_("Golden snapshot"))
		manifest = snapshot(students=students, seed=seed)
	return {
		"site": frappe.local.site,
		"created": data.get("created") or {},
		"errors": data.get("errors") or [],
		"password": password,
		"otp": demo_otp(),
		"accounts": accounts,
		"snapshot": manifest,
	}


def check_only_demo_data() -> None:
	"""Refuse a site that holds real records: the demonstration is open to anyone."""
	real_students = frappe.db.count("Hostel Student", {"register_number": ["not like", "UAT%"]})
	if real_students:
		raise DemoSiteError(
			_(
				"This site holds {0} students that are not demonstration records (their register "
				"numbers do not start with UAT). A demonstration site is open to anyone, so it can only "
				"be prepared on a site that holds nothing but demonstration data. Use a new site."
			).format(real_students)
		)
	real_staff = frappe.db.count("Hostel Staff", {"email": ["not like", f"%@{STAFF_DOMAIN}"]})
	if real_staff:
		raise DemoSiteError(
			_(
				"This site holds {0} staff records that are not demonstration accounts. Use a new site for "
				"the demonstration."
			).format(real_staff)
		)


def run_site_setup(portal_url: str) -> None:
	from hostel360.hostel_migration.site_setup import SiteSetupError, setup_site

	try:
		setup_site(
			institution_name=INSTITUTION_NAME,
			short_name=SHORT_NAME,
			admin_email=ADMIN_LOGIN,
			portal_url=portal_url,
			admin_name=STAFF[0][1],
			office_email=f"office@{STAFF_DOMAIN}",
			# No System Manager among the demo accounts: Administrator stays the only one.
			system_manager=False,
		)
	except SiteSetupError as e:
		raise DemoSiteError(str(e)) from e


def apply_safe_settings() -> None:
	"""Settings that make an open, fictitious site safe and easy to try."""
	from hostel360.setup.install import apply_settings

	apply_settings(
		"System Settings",
		{
			# Anyone may try any account, so a second factor would only lock visitors out.
			"enable_two_factor_auth": 0,
			# The shared password must not expire: the snapshot keeps the reset date.
			"force_user_to_reset_password": 0,
		},
	)
	apply_settings("Website Settings", {"home_page": "demo"})
	apply_settings(
		"Hostel Settings",
		{
			"institution_address": INSTITUTION_ADDRESS,
			"office_phone": "+91 90000 00800",
			"office_email": f"office@{STAFF_DOMAIN}",
			"bonafide_signatory_name": STAFF[1][1],
			"bonafide_signatory_designation": "Chief Warden",
		},
	)
	# Channels on, so every notification is logged (as not sent, see hostel360.demo); test
	# mode on as well, in case the site ever leaves demonstration mode.
	apply_settings(
		"Messaging Settings",
		{
			"sms_enabled": 1,
			"whatsapp_enabled": 1,
			"email_enabled": 1,
			"test_mode": 1,
			"otp_channels": "SMS and WhatsApp",
			"otp_email_enabled": 0,
		},
	)
	apply_settings("Hostel Payment Settings", {"enabled": 0, "test_mode": 1})
	configure_operations()


def configure_operations() -> None:
	"""Alerting stays on (the desk shows how it works) with fictitious contacts only."""
	settings = frappe.get_single("Ops Alert Settings")
	settings.enabled = 1
	settings.environment = "Staging"
	settings.instance_label = "Hostel360 demonstration"
	settings.heartbeat_url = ""
	settings.report_recipients = f"principal@{STAFF_DOMAIN}\nchief.warden@{STAFF_DOMAIN}"
	settings.set(
		"channels",
		[
			{
				"channel_type": "Email",
				"target": f"operations@{STAFF_DOMAIN}",
				"min_severity": "Warning",
				"enabled": 1,
			}
		],
	)
	contacts = {
		1: (f"helpdesk@{STAFF_DOMAIN}", "9000000901"),
		2: (f"tech.lead@{STAFF_DOMAIN}", "9000000902"),
		3: (f"director@{STAFF_DOMAIN}", "9000000903"),
	}
	for row in settings.get("escalation_levels") or []:
		email, mobile = contacts.get(cint(row.level), (f"level{cint(row.level)}@{STAFF_DOMAIN}", ""))
		row.notify_emails = email
		row.notify_mobiles = mobile
	settings.flags.ignore_permissions = True
	settings.flags.ignore_mandatory = True
	settings.save()


def new_password() -> str:
	"""Easy to read out and type, and still strong enough for the password policy."""
	from frappe.utils.password_strength import test_password_strength

	minimum = cint(frappe.db.get_single_value("System Settings", "minimum_password_score")) or 3
	while True:
		first, second = secrets.choice(PASSWORD_WORDS), secrets.choice(PASSWORD_WORDS)
		if first == second:
			continue
		password = f"{first}-{second}-{secrets.randbelow(9000) + 1000}"
		if cint(test_password_strength(password).get("score")) >= minimum:
			return password


def ensure_staff_accounts(password: str) -> list[dict]:
	rows = []
	for index, (login, name, role, designation, hostels, note) in enumerate(STAFF, start=1):
		email = f"{login}@{STAFF_DOMAIN}"
		if designation:
			user = ensure_staff_record(email, name, designation, hostels, f"{STAFF_MOBILE_PREFIX}{index:02d}")
		else:
			user = ensure_user(email, name, role)
		configure_login(user, password)
		rows.append({"user": user, "kind": "staff", "role": role, "name": name, "note": note})
	return rows


def ensure_staff_record(email: str, name: str, designation: str, hostels, mobile: str) -> str:
	existing = frappe.db.get_value("Hostel Staff", {"email": email}, "name")
	doc = frappe.get_doc("Hostel Staff", existing) if existing else frappe.new_doc("Hostel Staff")
	doc.update(
		{
			"staff_name": name,
			"employee_id": f"DEMO-{email.split('@')[0].replace('.', '-').upper()}",
			"designation": designation,
			"mobile_number": mobile,
			"email": email,
			"is_active": 1,
			"create_user": 1,
			"send_welcome_email": 0,
		}
	)
	doc.set("assignments", [{"hostel": hostel} for hostel in hostels if frappe.db.exists("Hostel", hostel)])
	doc.flags.ignore_permissions = True
	doc.save()
	user = frappe.db.get_value("Hostel Staff", doc.name, "user")
	if not user:
		raise DemoSiteError(_("The desk login for {0} could not be created.").format(email))
	return user


def ensure_user(email: str, name: str, role: str) -> str:
	if frappe.db.exists("User", email):
		user = frappe.get_doc("User", email)
		if role not in {row.role for row in user.roles}:
			user.append("roles", {"role": role})
			user.flags.ignore_permissions = True
			user.save()
		return user.name
	first_name, _sep, last_name = name.partition(" ")
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": first_name,
			"last_name": last_name,
			"user_type": "System User",
			"send_welcome_email": 0,
			"roles": [{"role": role}],
		}
	)
	user.flags.ignore_permissions = True
	user.flags.no_welcome_mail = True
	user.insert()
	return user.name


def configure_login(user: str, password: str | None) -> None:
	from frappe.utils.password import update_password

	frappe.db.set_value(
		"User",
		user,
		{"enabled": 1, "simultaneous_sessions": DEMO_SIMULTANEOUS_SESSIONS, "send_welcome_email": 0},
		update_modified=False,
	)
	if password:
		update_password(user, password)
		frappe.db.set_value("User", user, "last_password_reset_date", nowdate(), update_modified=False)


# --------------------------------------------------------------------------- portal accounts


def ensure_portal_accounts() -> list[dict]:
	"""Three students in three hostels and two parents, each with a story to try."""
	from hostel360.hostel_people.services import ensure_portal_user

	previous_migration = frappe.flags.hostel360_migration
	frappe.flags.hostel360_migration = True
	try:
		dues = pick_student("student_dues", "BH1", with_dues=True)
		leave = pick_student("student_leave", "BH2", exclude=[dues])
		girls = pick_student("student_girls", "GH1", exclude=[dues, leave])
		chosen = [s for s in (dues, leave, girls) if s]
		if len(chosen) < 3:
			raise DemoSiteError(
				_("Not enough residents to choose the demonstration students from. Use more --students.")
			)
		leave_parent = pick_parent("parent_leave", leave)
		two_wards_parent = pick_parent("parent_two_wards", dues)
		link_second_ward(two_wards_parent, girls, sibling=dues)
		ensure_story_leave(leave, leave_parent)
		record_consent([dues, leave, girls], [leave_parent, two_wards_parent])

		rows = []
		for key, student, story in (
			("student_dues", dues, "dues"),
			("student_leave", leave, "leave"),
			("student_girls", girls, None),
		):
			info = frappe.db.get_value(
				"Hostel Student", student, ["full_name", "current_hostel"], as_dict=True
			)
			hostel = frappe.db.get_value("Hostel", info.current_hostel, "hostel_name")
			role = _("Student, {0}").format(hostel) + {
				"dues": " - " + _("fees due"),
				"leave": " - " + _("leave awaiting the parent"),
			}.get(story, "")
			user = ensure_portal_user("Hostel Student", student)
			configure_login(user, None)
			rows.append(
				{
					"user": user,
					"kind": "student",
					"role": role,
					"name": info.full_name,
					"mobile": PORTAL_MOBILES[key],
					"hostel": hostel,
					"note": story,
				}
			)
		for key, guardian, wards, story in (
			("parent_leave", leave_parent, [leave], "approve_leave"),
			("parent_two_wards", two_wards_parent, [dues, girls], "two_wards"),
		):
			name = frappe.db.get_value("Hostel Guardian", guardian, "guardian_name")
			ward_names = [frappe.db.get_value("Hostel Student", ward, "first_name") for ward in wards]
			user = ensure_portal_user("Hostel Guardian", guardian)
			configure_login(user, None)
			rows.append(
				{
					"user": user,
					"kind": "parent",
					"role": _("Parent of {0}").format(join_names(ward_names)),
					"name": name,
					"mobile": PORTAL_MOBILES[key],
					"hostel": None,
					"note": story,
					"wards": ward_names,
				}
			)
		return rows
	finally:
		frappe.flags.hostel360_migration = previous_migration


def join_names(names: list[str]) -> str:
	names = [name for name in names if name]
	if len(names) < 2:
		return "".join(names)
	return "{0} {1} {2}".format(", ".join(names[:-1]), _("and"), names[-1])


def pick_student(key: str, hostel: str, with_dues: bool = False, exclude=None) -> str | None:
	"""The resident already holding the demo number, else the first suitable one."""
	mobile = PORTAL_MOBILES[key]
	existing = frappe.db.get_value("Hostel Student", {"mobile_number": mobile}, "name")
	if existing:
		return existing
	exclude = [name for name in (exclude or []) if name]
	busy = set(
		frappe.get_all(
			"Home Visit Leave",
			filters={"status": ["not in", ["Cancelled", "Rejected", "Returned"]]},
			pluck="student",
		)
	)
	candidates = frappe.get_all(
		"Hostel Student",
		filters={
			"register_number": ["like", "UAT%"],
			"current_hostel": hostel,
			"status": "Active",
			"name": ["not in", exclude or [""]],
		},
		pluck="name",
		order_by="name asc",
	)
	if with_dues:
		owing = set(
			frappe.get_all(
				"Hostel Fee Invoice",
				filters={"docstatus": 1, "outstanding_amount": [">", 0], "invoice_type": "Hostel Fee"},
				pluck="student",
			)
		)
		candidates = [c for c in candidates if c in owing] or candidates
	student = next((c for c in candidates if c not in busy), None) or (candidates[0] if candidates else None)
	if student:
		take_number("Hostel Student", student, mobile)
	return student


def pick_parent(key: str, student: str) -> str:
	mobile = PORTAL_MOBILES[key]
	existing = frappe.db.get_value("Hostel Guardian", {"mobile_number": mobile}, "name")
	if existing:
		return existing
	guardian = frappe.db.get_value(
		"Hostel Student Guardian",
		{"parent": student, "parenttype": "Hostel Student", "is_primary": 1},
		"guardian",
	) or frappe.db.get_value(
		"Hostel Student Guardian", {"parent": student, "parenttype": "Hostel Student"}, "guardian"
	)
	if not guardian:
		raise DemoSiteError(_("Student {0} has no guardian to publish as a demo parent.").format(student))
	take_number("Hostel Guardian", guardian, mobile)
	return guardian


def take_number(doctype: str, name: str, mobile: str) -> None:
	"""Give a record the published demo number (taking it from any other record first)."""
	for holder in frappe.get_all(
		doctype, filters={"mobile_number": mobile, "name": ["!=", name]}, pluck="name"
	):
		frappe.db.set_value(doctype, holder, "mobile_number", None, update_modified=False)
	frappe.db.set_value(doctype, name, "mobile_number", mobile, update_modified=False)
	if doctype == "Hostel Guardian":
		frappe.db.set_value(
			"Hostel Student Guardian", {"guardian": name}, "mobile_number", mobile, update_modified=False
		)


def link_second_ward(guardian: str, student: str, sibling: str) -> None:
	"""Make ``guardian`` the primary guardian of ``student`` too (brother and sister)."""
	doc = frappe.get_doc("Hostel Student", student)
	if any(row.guardian == guardian for row in doc.guardians):
		return
	relation = frappe.db.get_value("Hostel Guardian", guardian, "relation") or "Father"
	for row in doc.guardians:
		row.is_primary = 0
	doc.append(
		"guardians",
		{"guardian": guardian, "relation": relation, "is_primary": 1, "can_approve_leave": 1},
	)
	surname = frappe.db.get_value("Hostel Student", sibling, "last_name")
	if surname:
		doc.last_name = surname
	doc.flags.ignore_permissions = True
	doc.save()


def ensure_story_leave(student: str, guardian: str) -> None:
	"""A leave from today waiting for the parent: the parent approves, then the deputy warden."""
	if frappe.db.exists("Home Visit Leave", {"student": student, "status": "Pending Parent Approval"}):
		return
	info = frappe.db.get_value(
		"Hostel Student", student, ["current_hostel", "current_room", "mobile_number"], as_dict=True
	)
	start = now_datetime().replace(second=0, microsecond=0) + datetime.timedelta(hours=5)
	if start.date() != getdate():
		start = now_datetime().replace(second=0, microsecond=0) + datetime.timedelta(minutes=45)
	frappe.get_doc(
		{
			"doctype": "Home Visit Leave",
			"student": student,
			"hostel": info.current_hostel,
			"room": info.current_room,
			"from_datetime": start,
			"to_datetime": start + datetime.timedelta(days=2, hours=3),
			"destination": "Home",
			"reason": "Cousin's wedding at Devakottai",
			"contact_during_leave": info.mobile_number,
			"applied_by": "Student",
			"parent_guardian": guardian,
			"status": "Pending Parent Approval",
			"parent_approval_status": "Pending",
			"warden_approval_status": "Pending",
		}
	).insert(ignore_permissions=True)


def record_consent(students: list[str], guardians: list[str]) -> None:
	"""Visitors land straight in the portal: the privacy notice and details are already confirmed."""
	version = frappe.db.get_single_value("Hostel Settings", "privacy_notice_version") or "1.0"
	now = now_datetime()
	for student in students:
		frappe.db.set_value(
			"Hostel Student",
			student,
			{"profile_confirmed": 1, "privacy_consent_version": version, "privacy_consent_on": now},
			update_modified=False,
		)
	for guardian in guardians:
		frappe.db.set_value(
			"Hostel Guardian",
			guardian,
			{"privacy_consent_version": version, "privacy_consent_on": now},
			update_modified=False,
		)


def assign_work(users_by_role: dict) -> None:
	"""Give the demo staff something on their desks: grievances, the messes, the hostels."""
	warden = users_by_role.get(WARDEN_ROLE)
	deputy = users_by_role.get(DEPUTY_WARDEN_ROLE)
	mess_manager = users_by_role.get(MESS_MANAGER_ROLE)
	office = users_by_role.get(OFFICE_ROLE)
	operations = users_by_role.get(OPERATIONS_ROLE)
	now = now_datetime()

	if warden:
		for hostel in frappe.get_all("Hostel", pluck="name"):
			frappe.db.set_value("Hostel", hostel, "warden", warden, update_modified=False)
	if mess_manager:
		for mess in frappe.get_all("Hostel Mess", pluck="name"):
			frappe.db.set_value("Hostel Mess", mess, "mess_manager", mess_manager, update_modified=False)

	open_grievances = frappe.get_all(
		"Hostel Grievance",
		filters={
			"status": ["in", ["Open", "In Progress", "Escalated", "Reopened"]],
			"assigned_to": ["is", "not set"],
		},
		fields=["name", "status", "category", "hostel"],
	)
	for row in open_grievances:
		if row.status == "Open":
			continue
		assignee = (
			mess_manager
			if row.category == "Mess and Food"
			else deputy
			if row.hostel == "BH2" and row.status != "Escalated"
			else warden
		)
		if assignee:
			frappe.db.set_value(
				"Hostel Grievance",
				row.name,
				{"assigned_to": assignee, "assigned_on": now},
				update_modified=False,
			)

	if office:
		office_name = frappe.db.get_value("User", office, "full_name")
		for ticket in frappe.get_all("Support Ticket", fields=["name", "status"]):
			values = {"raised_by": office, "raised_by_name": office_name}
			if operations and ticket.status != "Open":
				values["assigned_to"] = operations
			frappe.db.set_value("Support Ticket", ticket.name, values, update_modified=False)


# --------------------------------------------------------------------------- reset


def snapshot(students: int | None = None, seed: int | None = None) -> dict:
	from hostel360.demo.snapshot import read_manifest, take_snapshot

	previous = read_manifest() or {}
	config = current_config()
	config["hostel360_demo_seed"] = (
		cint(seed or previous.get("config", {}).get("hostel360_demo_seed")) or DEFAULT_SEED
	)
	config["hostel360_demo_students"] = (
		cint(students or previous.get("config", {}).get("hostel360_demo_students")) or DEFAULT_STUDENTS
	)
	return take_snapshot(config)


def reset_demo_site(progress=None) -> dict:
	"""Restore the golden snapshot and bring it to today. See the module docstring."""
	from hostel360.demo.redate import interval, shift_dates
	from hostel360.demo.snapshot import app_versions, read_manifest, restore_snapshot
	from hostel360.hostel_migration.demo_data import refresh_calendar

	say = progress or (lambda message: None)
	if not is_demo_site():
		raise DemoSiteError(
			_(
				"This site is not a demonstration site. h360-demo-reset only restores sites prepared "
				"with h360-demo-site."
			)
		)
	manifest = read_manifest()
	if not manifest:
		raise DemoSiteError(_("This site has no demonstration snapshot yet. Run h360-demo-site first."))
	site = frappe.local.site
	config = {key: value for key, value in (manifest.get("config") or {}).items() if key in CONFIG_KEYS}
	config[DEMO_MODE_KEY] = 1

	with demo_lock():
		set_config({"maintenance_mode": 1})
		loading = False
		try:
			if not frappe.flags.in_test:
				# Web workers and the scheduler read site_config.json at most a minute late;
				# let them all see maintenance mode before the tables are dropped.
				say(_("Waiting for the site to stop taking requests"))
				time.sleep(MAINTENANCE_GRACE_SECONDS)
			say(_("Restoring the snapshot of {0}").format(manifest.get("taken_at")))
			loading = True
			restore_snapshot(manifest)
			set_config(config)
			migrated = False
			if manifest.get("apps") != app_versions():
				say(_("The code changed since the snapshot: migrating"))
				migrate(site)
				migrated = True

			seconds, days = interval(manifest["taken_at"])
			say(_("Moving dates forward by {0} days").format(days))
			touched = shift_dates(seconds, days)
			frappe.db.commit()  # nosemgrep

			say(_("This week's menus and last month's bills"))
			refreshed = refresh_calendar(
				seed=cint((manifest.get("config") or {}).get("hostel360_demo_seed")) or DEFAULT_SEED
			)
			sign_everyone_out()
			forget_cached_state()
			frappe.db.commit()  # nosemgrep
			if migrated:
				say(_("Taking a new snapshot for the migrated code"))
				snapshot()
		except Exception:
			if loading:
				# Half a restore must not be served to visitors: the site stays in maintenance
				# mode until a reset succeeds.
				frappe.logger("hostel360").error(
					"Demonstration reset failed during the restore; the site stays in maintenance mode",
					exc_info=True,
				)
				raise
			set_config({"maintenance_mode": None})
			raise
		set_config({"maintenance_mode": None})
		frappe.clear_cache()
	return {
		"site": site,
		"snapshot_taken_at": manifest.get("taken_at"),
		"days": days,
		"rows_shifted": sum(touched.values()),
		"created": refreshed.get("created") or {},
		"errors": refreshed.get("errors") or [],
		"migrated": migrated,
	}


def migrate(site: str) -> None:
	from frappe.migrate import SiteMigration

	frappe.db.commit()  # nosemgrep
	SiteMigration().run(site=site)
	# SiteMigration ends with frappe.destroy(); carry on with a fresh connection.
	frappe.init(site)
	frappe.connect()


def sign_everyone_out() -> None:
	frappe.db.delete("Sessions")
	frappe.cache.delete_value("session")


def forget_cached_state() -> None:
	"""Sign-in counters and locks, provider breakers: none of it belongs to the restored site."""
	frappe.cache.delete_keys("hostel360:")
