"""Install hook for the Hostel Migration module.

The module needs no default records: migration runs and record maps are created by the
people (or deploy scripts) that run a migration. ``install_defaults`` exists so the
install sequence treats every module alike and is safe to call on every migrate.
"""


def install_defaults() -> None:
	return None
