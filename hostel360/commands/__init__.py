"""Bench commands shipped with Hostel360.

    bench --site <site> h360-setup --institution-name ... --short-name ... --admin-email ... --portal-url ...
    bench --site <site> h360-migrate --source DIR --mapping FILE [--dry-run] [--entity NAME ...]
    bench --site <site> h360-export --output DIR [--no-files] [--encrypt] [--passphrase-file FILE]
    bench h360-verify-export --path DIR_OR_ARCHIVE [--passphrase-file FILE] [--extract-to DIR]
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
@click.option("--reset-password", is_flag=True, default=False, help="Give an existing administrator a new password.")
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
		click.echo("Two-factor sign-in is required: the administrator enrols an authenticator app at first login.")
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

	sys.exit(EXIT_OK if status == "Completed" else EXIT_ATTENTION if status == "Completed with Errors" else EXIT_FAILED)


# ------------------------------------------------------------------------ export


@click.command("h360-export")
@click.option("--output", required=True, help="Folder to write the export into (outside the public folder).")
@click.option("--include-files/--no-files", default=True, show_default=True, help="Include uploaded files.")
@click.option("--encrypt", is_flag=True, default=False, help="Encrypt the archive with a passphrase (AES-256-GCM).")
@click.option("--passphrase-file", default=None, help="Read the passphrase from this file instead of prompting.")
@click.option("--archive/--no-archive", default=True, show_default=True, help="Pack the export into a .tar.gz.")
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

	_connect(context)
	try:
		result = run_exit_export(
			resolve_cli_path(output),
			include_files=include_files,
			encrypt=encrypt,
			passphrase=passphrase,
			archive=archive,
			progress=_progress,
		)
	except ExportError as e:
		raise click.ClickException(str(e)) from e
	finally:
		frappe.destroy()

	click.secho(f"Export {result['name']} complete: {result['entries']} files, {result['bytes']:,} bytes.", fg="green")
	if result["directory"]:
		click.echo(f"Folder  : {result['directory']}")
	if result["archive"]:
		click.echo(f"Archive : {result['archive']}")
		click.echo(f"SHA-256 : {result['archive_sha256']}")
	if result["encrypted"]:
		click.secho("The archive is encrypted. Without the passphrase it cannot be opened.", fg="yellow")


@click.command("h360-verify-export")
@click.option("--path", "path", required=True, help="Export folder, .tar.gz archive or encrypted archive.")
@click.option("--passphrase-file", default=None, help="Passphrase for an encrypted archive.")
@click.option("--extract-to", default=None, help="Keep the decrypted, unpacked export in this folder.")
def h360_verify_export(path, passphrase_file, extract_to):
	"""Check an exit export against its manifest (no site needed)."""
	from hostel360.hostel_migration import crypto
	from hostel360.hostel_migration.verify import verify_export

	target = resolve_cli_path(path)
	passphrase = None
	if os.path.isfile(target) and crypto.is_encrypted(target):
		passphrase = read_passphrase(passphrase_file, confirm=False)

	result = verify_export(target, passphrase=passphrase, extract_to=resolve_cli_path(extract_to) if extract_to else None)
	if result.export_name:
		click.echo(f"Export   : {result.export_name} of {result.site}, created {result.created_on}")
	if result.archive_checksum:
		click.echo(f"SHA-256  : {result.archive_checksum}")
	click.echo(f"Verified : {result.checked} files, {result.bytes:,} bytes")
	if result.extracted_to:
		click.echo(f"Unpacked : {result.extracted_to}")
	if result.ok:
		click.secho("OK: every file matches the manifest.", fg="green")
		sys.exit(EXIT_OK)
	for problem in result.problems[:200]:
		click.secho(f"  {problem}", fg="red")
	if len(result.problems) > 200:
		click.echo(f"  ... and {len(result.problems) - 200} more")
	click.secho("FAILED: the export could not be verified.", fg="red", bold=True)
	sys.exit(EXIT_FAILED)


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


commands = [h360_setup, h360_migrate, h360_export, h360_verify_export, h360_demo_data]
