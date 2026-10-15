"""Install-time defaults for Student Services."""


def install_defaults():
	"""Student Services has no seed data, so this is safe to run on every migrate.

	Its DocTypes, the Bonafide Certificate print format, the reports and the approvals page
	are synced from the app by migrate, and the message templates for its events are
	installed by Hostel Communication.
	"""
