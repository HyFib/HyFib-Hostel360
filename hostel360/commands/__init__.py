"""Bench commands shipped with Hostel360.

    bench --site <site> h360-setup --institution-name ... --short-name ... --admin-email ... --portal-url ...
    bench --site <site> h360-migrate --source DIR --mapping FILE [--dry-run] [--entity NAME ...]
    bench --site <site> h360-export --output DIR [--no-files] [--encrypt] [--passphrase-file FILE]
    bench [--site <site>] h360-verify-export --path DIR_OR_ARCHIVE [--passphrase-file FILE] [--extract-to DIR]
    bench --site <site> h360-purge-institution-data --confirm-site <site> --acknowledgement-reference REF [--execute]
    bench [--site <site>] h360-verify-deletion-certificate --path FILE [--key-file FILE]
    bench --site <site> h360-demo-data [--students N] [--seed N]

Exit codes: 0 success; 1 failure; 2 finished but with rows or checks that need attention.
"""

import os
import stat
import sys

import click
from frappe.commands import get_site, pass_context

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ATTENTION = 2


def _connect(context) -> str:
	import frappe

	site = get_site(context)
	frappe.init(site)
	frappe.connect()
	return site


def resolve_cli_path(path: str) -> str:
	"""Bench runs commands from the ``sites`` folder; accept paths relative to where the
	operator typed the command or to the bench folder as well."""
	path = os.path.expanduser(path)
	if os.path.isabs(path):
		return path
	candidates = []
	if os.environ.get("PWD"):
		candidates.append(os.path.join(os.environ["PWD"], path))
	candidates += [os.path.abspath(path), os.path.abspath(os.path.join("..", path))]
	for candidate in candidates:
		if os.path.exists(candidate):
			return os.path.realpath(candidate)
	return os.path.abspath(candidates[0])


def read_passphrase(passphrase_file: str | None, confirm: bool) -> str:
	if passphrase_file:
		path = resolve_cli_path(passphrase_file)
		mode = os.stat(path).st_mode
		if mode & (stat.S_IRWXG | stat.S_IRWXO):
			click.secho(f"Warning: {path} is readable by other users; chmod 600 it.", fg="yellow", err=True)
		with open(path, encoding="utf-8") as f:
			passphrase = f.readline().rstrip("\r\n")
		if not passphrase:
			raise click.ClickException(f"{path} is empty.")
		return passphrase
	return click.prompt("Passphrase", hide_input=True, confirmation_prompt=confirm)


def _progress(message: str) -> None:
	click.echo(message)


# ------------------------------------------------------------------------ setup


@click.command("h360-setup")
@click.option("--institution-name", required=True, help="Full name of the institution.")
@click.option("--short-name", required=True, help="Short name shown in the portal, e.g. ACGCET.")
@click.option("--admin-email", required=True, help="Login e-mail of the first Hostel Administrator.")
@click.option("--portal-url", required=True, help="Public address, e.g. https://hostel.example.ac.in")
@click.option("--admin-name", default=None, help="Administrator's full name.")
@click.option("--office-email", default=None, help="Hostel office e-mail shown to students and parents.")
@click.option(
	"--system-manager/--no-system-manager",
	default=True,
	show_default=True,
	help="Also make the administrator a System Manager (needed for e-mail accounts and users).",
)
@click.option(
	"--reset-password", is_flag=True, default=False, help="Give an existing administrator a new password."
)
@pass_context
def h360_setup(
	context,
	institution_name,
	short_name,
	admin_email,
	portal_url,
	admin_name,
	office_email,
	system_manager,
	reset_password,
):
	"""Set up a new Hostel360 site without the setup wizard."""
	import frappe

	from hostel360.hostel_migration.site_setup import SiteSetupError, setup_site

	_connect(context)
	try:
		result = setup_site(
			institution_name=institution_name,
			short_name=short_name,
			admin_email=admin_email,
			portal_url=portal_url,
			admin_name=admin_name,
			office_email=office_email,
			system_manager=system_manager,
			reset_password=reset_password,
		)
	except SiteSetupError as e:
		raise click.ClickException(str(e)) from e
	finally:
		frappe.destroy()

	click.secho(f"Hostel360 is set up on {result['site']} for {result['institution_name']}.", fg="green")
	click.echo(f"Portal URL     : {result['portal_url']}")
	click.echo(f"Administrator  : {result['admin_user']} ({', '.join(result['roles'])})")
	if result["admin_password"]:
		click.echo("")
		click.secho("Initial password (shown only now; store it in the password vault):", fg="yellow")
		click.secho(f"    {result['admin_password']}", bold=True)
		click.echo(
			"Two-factor sign-in is required: the administrator enrols an authenticator app at first login."
		)
	else:
		click.echo("The administrator already existed; the password was not changed (use --reset-password).")


# ----------------------------------------------------------------------- migrate


@click.command("h360-migrate")
@click.option("--source", required=True, help="Folder with the legacy CSV/XLSX files and attachments.")
@click.option("--mapping", required=True, help="Mapping JSON file.")
@click.option("--dry-run", is_flag=True, default=False, help="Validate everything, report, then roll back.")
@click.option("--entity", "entities", multiple=True, help="Only this entity (repeat for several).")
@click.option("--title", default=None, help="Title of the Migration Run.")
@pass_context
def h360_migrate(context, source, mapping, dry_run, entities, title):
	"""Load legacy hostel data into Hostel360 (idempotent; safe to repeat)."""
	import frappe

	from hostel360.hostel_migration.engine import MigrationLocked, create_run, execute_run
	from hostel360.hostel_migration.mapping import MappingError

	source_dir = resolve_cli_path(source)
	mapping_file = resolve_cli_path(mapping)
	if not os.path.isdir(source_dir):
		raise click.ClickException(f"Source folder {source} was not found.")
	if not os.path.isfile(mapping_file):
		raise click.ClickException(f"Mapping file {mapping} was not found.")

	_connect(context)
	try:
		try:
			run = create_run(source_dir, mapping_file, dry_run=dry_run, entities=list(entities), title=title)
		except (MappingError, frappe.ValidationError) as e:
			raise click.ClickException(str(e)) from e
		click.echo(f"Migration Run {run}{' (dry run)' if dry_run else ''}")
		try:
			status = execute_run(run, restrict_source=False, progress=_progress)
		except MigrationLocked as e:
			raise click.ClickException(str(e)) from e

		doc = frappe.get_doc("Migration Run", run)
		click.echo("")
		colour = {"Completed": "green", "Completed with Errors": "yellow"}.get(status, "red")
		click.secho(f"{status}: reconciliation {doc.reconciliation_status}", fg=colour, bold=True)
		click.echo(
			f"{doc.total_source_rows} rows: {doc.total_created} created, {doc.total_updated} updated, "
			f"{doc.total_skipped} unchanged, {doc.total_failed} failed"
		)
		for row in doc.entities:
			click.echo(
				f"  {row.entity:<22} {row.status:<22} rows {row.source_rows:>6}  in Hostel360 {row.target_count:>6}"
				f"  failed {row.failed:>4}"
			)
		if doc.report_file:
			click.echo(f"Report: {frappe.utils.get_url(doc.report_file)}")
	finally:
		frappe.destroy()

	sys.exit(
		EXIT_OK
		if status == "Completed"
		else EXIT_ATTENTION
		if status == "Completed with Errors"
		else EXIT_FAILED
	)


# ------------------------------------------------------------------------ export


@click.command("h360-export")
@click.option("--output", required=True, help="Folder to write the export into (outside the public folder).")
@click.option("--include-files/--no-files", default=True, show_default=True, help="Include uploaded files.")
@click.option(
	"--encrypt", is_flag=True, default=False, help="Encrypt the archive with a passphrase (AES-256-GCM)."
)
@click.option(
	"--passphrase-file", default=None, help="Read the passphrase from this file instead of prompting."
)
@click.option(
	"--archive/--no-archive", default=True, show_default=True, help="Pack the export into a .tar.gz."
)
@pass_context
def h360_export(context, output, include_files, encrypt, passphrase_file, archive):
	"""Export all Hostel360 data (CSV, Excel, SQL dump, files) for handover at contract exit."""
	import frappe

	from hostel360.hostel_migration import crypto
	from hostel360.hostel_migration.export import ExportError, run_exit_export

	passphrase = None
	if encrypt:
		passphrase = read_passphrase(passphrase_file, confirm=True)
		try:
			crypto.check_passphrase(passphrase)
		except ValueError as e:
			raise click.ClickException(str(e)) from e

	site = _connect(context)
	try:
		result = run_exit_export(
			resolve_cli_path(output),
			include_files=include_files,
			encrypt=encrypt,
			passphrase=passphrase,
			archive=archive,
			progress=_progress,
		)
		frappe.db.commit()
	except ExportError as e:
		raise click.ClickException(str(e)) from e
	finally:
		frappe.destroy()

	click.secho(
		f"Export {result['name']} complete: {result['entries']} files, {result['bytes']:,} bytes.", fg="green"
	)
	if result["directory"]:
		click.echo(f"Folder  : {result['directory']}")
	if result["archive"]:
		click.echo(f"Archive : {result['archive']}")
		click.echo(f"SHA-256 : {result['archive_sha256']}")
	if result["encrypted"]:
		click.secho("The archive is encrypted. Without the passphrase it cannot be opened.", fg="yellow")
	if result.get("log"):
		click.echo(f"Logged  : Exit Export Log {result['log']} (not verified yet)")
	else:
		click.secho("Warning: the export could not be logged on the site (see Error Log).", fg="yellow")
	if not include_files:
		click.secho(
			"This export leaves out uploaded files; it cannot clear the way for deleting the data.",
			fg="yellow",
		)
	click.echo("Verify it, so the site records the check:")
	click.echo(
		f"    bench --site {site} h360-verify-export --path {result['archive'] or result['directory']}"
	)


@click.command("h360-verify-export")
@click.option("--path", "path", required=True, help="Export folder, .tar.gz archive or encrypted archive.")
@click.option("--passphrase-file", default=None, help="Passphrase for an encrypted archive.")
@click.option("--extract-to", default=None, help="Keep the decrypted, unpacked export in this folder.")
@pass_context
def h360_verify_export(context, path, passphrase_file, extract_to):
	"""Check an exit export against its manifest.

	No site is needed for the check itself. With --site, the result is also recorded in
	that site's Exit Export Log, which the deletion after exit requires.
	"""
	from hostel360.hostel_migration import crypto
	from hostel360.hostel_migration.verify import verify_export

	target = resolve_cli_path(path)
	passphrase = None
	if os.path.isfile(target) and crypto.is_encrypted(target):
		passphrase = read_passphrase(passphrase_file, confirm=False)

	result = verify_export(
		target, passphrase=passphrase, extract_to=resolve_cli_path(extract_to) if extract_to else None
	)
	if result.export_name:
		click.echo(f"Export   : {result.export_name} of {result.site}, created {result.created_on}")
	if result.archive_checksum:
		click.echo(f"SHA-256  : {result.archive_checksum}")
	click.echo(f"Verified : {result.checked} files, {result.bytes:,} bytes")
	if result.extracted_to:
		click.echo(f"Unpacked : {result.extracted_to}")
	if result.manifest_sha256:
		click.echo(f"Manifest : SHA-256 {result.manifest_sha256}")
	recorded_ok = _record_verification(context, result)
	if result.ok and recorded_ok is not False:
		click.secho("OK: every file matches the manifest.", fg="green")
		sys.exit(EXIT_OK)
	if result.ok:
		click.secho(
			"The files match the manifest, but the site does not accept this copy (see above).", fg="red"
		)
		sys.exit(EXIT_FAILED)
	for problem in result.problems[:200]:
		click.secho(f"  {problem}", fg="red")
	if len(result.problems) > 200:
		click.echo(f"  ... and {len(result.problems) - 200} more")
	click.secho("FAILED: the export could not be verified.", fg="red", bold=True)
	sys.exit(EXIT_FAILED)


def _record_verification(context, result) -> bool | None:
	"""Record the outcome on the site given with --site. None: no site, nothing recorded."""
	import frappe

	site = get_site(context, raise_err=False)
	if not site:
		click.echo("Not recorded: run with --site <site> to record the check on the site.")
		return None
	from hostel360.hostel_migration.doctype.exit_export_log.exit_export_log import record_verification

	try:
		frappe.init(site)
		frappe.connect()
		name, message = record_verification(result, site=site)
		status = frappe.db.get_value("Exit Export Log", name, "status") if name else None
		frappe.db.commit()
	except Exception as e:
		click.secho(f"Not recorded on {site}: {e}", fg="yellow")
		return None
	finally:
		frappe.destroy()
	click.secho(message, fg="green" if status == "Verified" else "yellow")
	return None if not name else status == "Verified"


# ------------------------------------------------------------------- exit purge


@click.command("h360-purge-institution-data")
@click.option("--confirm-site", required=True, help="The site name again; it must match --site.")
@click.option(
	"--acknowledgement-reference",
	required=True,
	help="Reference of the institution's written confirmation that it received the exit export.",
)
@click.option(
	"--execute",
	is_flag=True,
	default=False,
	help="Delete. Without it nothing is deleted; the command only counts.",
)
@click.option(
	"--force-without-export",
	is_flag=True,
	default=False,
	help="Go ahead without a verified, complete export from the last 30 days (stated on the certificate).",
)
@click.option(
	"--keep-export-copy", is_flag=True, default=False, help="Leave this server's copy of the exit export."
)
@click.option(
	"--certificate-dir",
	default=None,
	help="Folder for the deletion certificate (default: <bench>/hostel360-exit).",
)
@click.option("--yes", is_flag=True, default=False, help="Do not ask for confirmation at the terminal.")
@pass_context
def h360_purge_institution_data(
	context,
	confirm_site,
	acknowledgement_reference,
	execute,
	force_without_export,
	keep_export_copy,
	certificate_dir,
	yes,
):
	"""Delete the institution's data after exit (TR 22) and issue a signed deletion certificate."""
	import frappe

	from hostel360.hostel_migration.purge import InstitutionDataPurge, PurgeRefused, clean_reference

	site = get_site(context)
	if confirm_site.strip() != site:
		raise click.ClickException(
			f"--confirm-site {confirm_site} does not match --site {site}; nothing was done."
		)
	try:
		reference = clean_reference(acknowledgement_reference)
	except PurgeRefused as e:
		raise click.ClickException(str(e)) from e

	_connect(context)
	try:
		job = InstitutionDataPurge(
			confirm_site,
			reference,
			force_without_export=force_without_export,
			keep_export_copy=keep_export_copy,
			certificate_dir=resolve_cli_path(certificate_dir) if certificate_dir else None,
			progress=_progress,
		)
		refusal = None
		try:
			checks = job.check()
		except PurgeRefused as e:
			checks, refusal = None, str(e)
		plan = job.plan(checks)
		_print_purge_plan(plan, checks)

		if refusal:
			click.secho(f"Refused: {refusal}", fg="red", bold=True)
			sys.exit(EXIT_FAILED)
		if not execute:
			click.secho("Dry run: nothing was deleted. Add --execute to delete.", fg="green", bold=True)
			sys.exit(EXIT_OK)
		if not yes and sys.stdin.isatty():
			click.confirm(
				f"Permanently delete the data listed above from {site}? This cannot be undone", abort=True
			)
		result = job.execute()
	except PurgeRefused as e:
		raise click.ClickException(str(e)) from e
	finally:
		frappe.destroy()

	paths = result["paths"]
	click.echo("")
	click.secho(f"Deletion certificate {result['certificate_id']}", bold=True)
	click.echo(f"  Signed JSON : {paths['json']}")
	click.echo(f"  Markdown    : {paths['markdown']}")
	if paths["pdf"]:
		click.echo(f"  PDF         : {paths['pdf']}")
	else:
		click.secho(
			f"  PDF         : not produced ({paths['pdf_error']}); the Markdown has the same content.",
			fg="yellow",
		)
	totals = result["totals"]
	click.echo(
		f"Deleted {totals['records']:,} records and {totals['files']:,} files ({totals['bytes']:,} bytes)."
	)
	if result["clean"]:
		click.secho("Checked afterwards: nothing selected for deletion remains.", fg="green")
	else:
		click.secho("Some items remain and need attention:", fg="yellow")
		for row in result["residual"]:
			click.echo(f"  {row['item']}: {row['remaining']}")
	click.echo("Finish the exit runbook for the stores outside the application listed on the certificate.")
	sys.exit(EXIT_OK if result["clean"] else EXIT_ATTENTION)


def _print_purge_plan(plan: dict, checks) -> None:
	deleted = plan["deleted"]
	click.secho(f"Site {plan['site']}: confirmation {plan['acknowledgement_reference']}", bold=True)
	if checks and checks.export:
		export = checks.export
		click.echo(
			f"Export   : {export.export_name}, made {str(export.exported_on)[:19]}, "
			f"verified {str(export.verified_on)[:19]}, manifest SHA-256 {export.manifest_sha256}"
		)
	if checks and checks.problem:
		click.secho(f"Forced   : {checks.problem}", fg="yellow")
	for warning in checks.warnings if checks else []:
		click.secho(f"Warning  : {warning}", fg="yellow")

	rows = [r for r in deleted["hostel360_doctypes"] if r["rows"]]
	click.echo("")
	click.secho(
		f"Hostel360 records ({sum(r['rows'] for r in rows):,} rows in {len(rows)} DocTypes):", bold=True
	)
	for row in rows:
		click.echo(f"  {row['doctype']:<40} {row['rows']:>10,}")
	click.secho("Related Frappe records:", bold=True)
	for row in deleted["frappe_records"]:
		click.echo(f"  {row['doctype']:<40} {row['rows']:>10,}")
	for row in deleted["internal_tables"]:
		click.echo(f"  {row['table']:<40} {row['rows']:>10,}")
	users = deleted["users"]
	kinds = ", ".join(f"{count} {kind}" for kind, count in sorted(users["by_kind"].items())) or "none"
	click.echo(f"User accounts: {users['count']:,} ({kinds})")
	kept_users = plan.get("retained_users") or []
	if kept_users:
		shown = ", ".join(kept_users[:15]) + (
			f" and {len(kept_users) - 15} more" if len(kept_users) > 15 else ""
		)
		click.echo(f"Accounts kept (Frappe standard and HyFib operations): {shown}")
	files = deleted["files"]
	click.echo(
		f"Uploaded files: {files['deleted']:,} ({files['bytes']:,} bytes), of which {files['orphans']:,} used by no record; "
		f"{files['kept']:,} kept for records that stay"
	)
	for key, label in (("local_backups", "Local backups"), ("migration_sources", "Legacy extracts")):
		item = deleted[key]
		click.echo(f"{label}: {item['files']:,} files ({item['bytes']:,} bytes) in {item['path']}")
	for copy in deleted["export_copies"]:
		click.echo(f"Export copy: {copy['path']} ({copy['result']})")
	changed = plan.get("changed_since_export") or []
	if changed:
		total = sum(r["rows"] for r in changed)
		top = ", ".join(f"{r['doctype']} {r['rows']}" for r in changed[:6])
		click.secho(
			f"Not in the export (added or changed after it was made): {total:,} records ({top})", fg="yellow"
		)
	click.echo(f"Certificate folder: {plan['certificate_dir']}")
	click.echo("")


@click.command("h360-verify-deletion-certificate")
@click.option("--path", "path", required=True, help="The certificate's .json file.")
@click.option(
	"--key-file", default=None, help="File holding the site's encryption_key (when the site is gone)."
)
@pass_context
def h360_verify_deletion_certificate(context, path, key_file):
	"""Check the signature of a deletion certificate."""
	import frappe

	from hostel360.hostel_migration import certificate

	try:
		document = certificate.load(resolve_cli_path(path))
		if key_file:
			with open(resolve_cli_path(key_file), encoding="utf-8") as f:
				key = certificate.signing_key(f.readline().strip())
		else:
			site = get_site(context, raise_err=False)
			if not site:
				raise click.ClickException(
					"Give --site <site>, or --key-file with the site's encryption_key."
				)
			_connect(context)
			try:
				key = certificate.signing_key()
			finally:
				frappe.destroy()
	except (certificate.CertificateError, OSError) as e:
		raise click.ClickException(str(e)) from e

	ok, message = certificate.verify(document, key)
	body = document.get("certificate") or {}
	click.echo(
		f"Certificate : {body.get('certificate_id')} for {body.get('site')}, finished {body.get('finished_at')}"
	)
	click.secho(message, fg="green" if ok else "red", bold=True)
	sys.exit(EXIT_OK if ok else EXIT_FAILED)


# --------------------------------------------------------------------- demo data


@click.command("h360-demo-data")
@click.option("--students", default=60, show_default=True, type=int, help="Number of demo students.")
@click.option("--seed", default=2026, show_default=True, type=int, help="Random seed; same seed, same data.")
@pass_context
def h360_demo_data(context, students, seed):
	"""Create (or top up) realistic demonstration / UAT data."""
	import frappe

	from hostel360.hostel_migration.demo_data import DemoDataError, generate

	_connect(context)
	try:
		result = generate(students=students, seed=seed, progress=_progress)
	except DemoDataError as e:
		raise click.ClickException(str(e)) from e
	finally:
		frappe.destroy()

	created = result["created"]
	if created:
		click.secho("Created:", fg="green")
		for doctype, count in sorted(created.items()):
			click.echo(f"  {doctype:<30} {count:>6}")
	else:
		click.echo("Nothing new to create; the demo data is already in place.")
	if result["errors"]:
		click.secho(f"{len(result['errors'])} records could not be created:", fg="yellow")
		for error in result["errors"][:50]:
			click.echo(f"  {error}")
		sys.exit(EXIT_ATTENTION)


commands = [
	h360_setup,
	h360_migrate,
	h360_export,
	h360_verify_export,
	h360_purge_institution_data,
	h360_verify_deletion_certificate,
	h360_demo_data,
]
