"""Tamil text in the print PDFs of the application image.

Run inside the image from the bench directory (deploy/tests/image/run.sh does this):

	env/bin/python render_tamil.py <sample html> <output dir>

Checks that fontconfig has the Noto Tamil faces and picks them for Tamil text, then renders
the sample through both PDF engines and looks at what each PDF really contains:

	wkhtmltopdf  through frappe.utils.pdf.get_pdf, the path of every Hostel360 print format
	Chromium     the binary Frappe's "chrome" generator starts (chromium_path, which
	             h360-configure sets), with Frappe's own sandbox and GPU switches

Both engines find fonts through fontconfig. Without a Tamil face they still produce a PDF,
but every Tamil character is silently left out, so the checks read the embedded fonts and the
text back from the PDF instead of trusting that rendering succeeded.

Prints one line per check ("ok" or "FAIL") and exits with the number of failed checks.
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from pypdf import PdfReader

SITE = "tamil-pdf-check.test"
# Same variable and default as deploy/docker/bin/h360-configure, which stores it as chromium_path.
CHROMIUM = os.environ.get("CHROMIUM_PATH") or "/usr/lib/chromium/chromium-headless-shell"

# Words of the sample that must come back out of Chromium's PDF text. Only words whose glyphs
# map one to one onto characters are used: pypdf reads the vowel signs that are drawn left of
# their consonant (கொ) in drawing order, and maps some combined glyphs (டி) to nothing.
# wkhtmltopdf (Qt 4) writes an even lossier map, so for it the Tamil characters are counted.
WORDS = ("ரசீது", "கட்டணம்", "மாணவர்", "ஸ்ரீ")

failures = 0


def report(passed: bool, label: str, detail: str = "") -> None:
	global failures
	if passed:
		print(f"ok    {label}")
	else:
		failures += 1
		print(f"FAIL  {label}")
		if detail:
			print(f"      {detail}")


def fontconfig(*args: str) -> str:
	# fixed fc-list / fc-match arguments, no shell
	return subprocess.run(args, check=True, capture_output=True, text=True).stdout  # noqa: S603


def embedded_fonts(pdf: bytes) -> set[str]:
	"""BaseFont names of every font the PDF embeds, without the subset prefix."""
	names = set()
	for page in PdfReader(io.BytesIO(pdf)).pages:
		fonts = page.get("/Resources", {}).get("/Font", {})
		for ref in fonts.values():
			name = str(ref.get_object().get("/BaseFont", ""))
			names.add(re.sub(r"^/?([A-Z]{6}\+)?", "", name))
	return names


def pdf_text(pdf: bytes) -> str:
	return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)


def tamil_characters(text: str) -> int:
	return sum(1 for char in text if 0x0B80 <= ord(char) <= 0x0BFF)  # the Tamil block


def check_pdf(engine: str, pdf: bytes, all_words: bool) -> None:
	fonts = embedded_fonts(pdf)
	text = pdf_text(pdf)
	shown = ", ".join(sorted(fonts)) or "none"
	report(
		"NotoSansTamil-Regular" in fonts,
		f"{engine}: Tamil in sans-serif and default text is drawn with Noto Sans Tamil",
		f"embedded fonts: {shown}",
	)
	report(
		"NotoSerifTamil-Regular" in fonts,
		f"{engine}: Tamil in serif text is drawn with Noto Serif Tamil",
		f"embedded fonts: {shown}",
	)
	if all_words:
		missing = [word for word in WORDS if word not in text]
		report(
			not missing, f"{engine}: the Tamil words read back from the PDF", f"missing: {', '.join(missing)}"
		)
	else:
		count = tamil_characters(text)
		report(
			count >= 20, f"{engine}: the PDF text carries Tamil characters ({count})", "expected at least 20"
		)


def render_wkhtmltopdf(html: str) -> bytes:
	import frappe

	# get_pdf needs an initialised site for its URL and option handling, but no database once
	# the page size is given, so a site directory of its own is enough.
	os.makedirs(os.path.join("sites", SITE, "logs"), exist_ok=True)
	with open(os.path.join("sites", SITE, "site_config.json"), "w") as handle:
		json.dump({"host_name": f"http://{SITE}"}, handle)
	os.chdir("sites")
	frappe.init(site=SITE, sites_path=".")
	try:
		from frappe.utils.pdf import get_pdf

		return get_pdf(html, {"page-size": "A4"})
	finally:
		frappe.destroy()
		os.chdir("..")
		shutil.rmtree(os.path.join("sites", SITE), ignore_errors=True)


def render_chromium(sample: str, output: str) -> bytes:
	profile = tempfile.mkdtemp(prefix="chromium-")
	try:
		# the image's own Chromium binary and fixed switches, no shell
		subprocess.run(  # noqa: S603
			[
				CHROMIUM,
				"--headless",
				"--no-sandbox",
				"--disable-gpu",
				"--disable-dev-shm-usage",
				f"--user-data-dir={profile}",
				"--no-pdf-header-footer",
				f"--print-to-pdf={output}",
				f"file://{os.path.abspath(sample)}",
			],
			check=True,
			capture_output=True,
			timeout=120,
		)
	finally:
		shutil.rmtree(profile, ignore_errors=True)
	with open(output, "rb") as handle:
		return handle.read()


def main(sample: str, out_dir: str) -> int:
	families = set(fontconfig("fc-list", ":lang=ta", "family").splitlines())
	for family in ("Noto Sans Tamil", "Noto Serif Tamil"):
		report(
			family in families,
			f"fontconfig has {family}",
			f"Tamil families: {', '.join(sorted(families)) or 'none'}",
		)
	for generic, expected in (("sans-serif", "Noto Sans Tamil"), ("serif", "Noto Serif Tamil")):
		chosen = fontconfig("fc-match", "-f", "%{family[0]}", f"{generic}:lang=ta")
		report(chosen == expected, f"fontconfig picks {expected} for Tamil {generic}", f"picked {chosen}")

	with open(sample, encoding="utf-8") as handle:
		html = handle.read()

	try:
		pdf = render_wkhtmltopdf(html)
	except Exception as error:
		report(False, "wkhtmltopdf: frappe.utils.pdf.get_pdf renders the sample", repr(error))
	else:
		with open(os.path.join(out_dir, "wkhtmltopdf.pdf"), "wb") as handle:
			handle.write(pdf)
		check_pdf("wkhtmltopdf", pdf, all_words=False)

	try:
		pdf = render_chromium(sample, os.path.join(out_dir, "chromium.pdf"))
	except (OSError, subprocess.SubprocessError) as error:
		stderr = getattr(error, "stderr", b"") or b""
		report(
			False, "chromium: the sample prints to PDF", f"{error!r} {stderr.decode(errors='replace')[-300:]}"
		)
	else:
		check_pdf("chromium", pdf, all_words=True)

	return failures


if __name__ == "__main__":
	if len(sys.argv) != 3:
		sys.exit("usage: render_tamil.py <sample html> <output dir>")
	sys.exit(main(sys.argv[1], sys.argv[2]))
