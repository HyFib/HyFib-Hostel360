#!/usr/bin/env bash
# Regression checks for Tamil text in print PDFs, run against a built application image.
#
#   deploy/tests/image/run.sh [image]   default: hostel360:<version>, the tag deploy/docker/build.sh
#                                       gives a local build; needs Docker, under a minute
#
# The image must carry fonts with Tamil glyphs (the base image has only DejaVu, and both PDF
# engines then drop every Tamil character without an error). Checks, see render_tamil.py:
#
#   - fontconfig has Noto Sans Tamil and Noto Serif Tamil and picks them for Tamil text
#   - wkhtmltopdf through frappe.utils.pdf.get_pdf embeds them and the PDF text has Tamil
#   - Chromium (Frappe's "chrome" generator) embeds them and the Tamil words read back
#
# The container starts through the image's own entrypoint, as the backend and the workers do.
# KEEP_OUTPUT=1 keeps the rendered PDFs and prints where; exit status is the number of failed
# checks.
set -Eeuo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "${here}/../../.." && pwd)"

image="${1:-${H360_TEST_IMAGE:-}}"
if [[ -z "${image}" ]]; then
	version="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "${repo}/hostel360/__init__.py")"
	image="hostel360:${version}"
fi
if ! docker image inspect "${image}" >/dev/null 2>&1; then
	echo "run.sh: image ${image} not found; build it with deploy/docker/build.sh or pass one" >&2
	exit 2
fi

work="$(mktemp -d)"
# shellcheck disable=SC2329 # run by the EXIT trap
cleanup() {
	if [[ "${KEEP_OUTPUT:-0}" == 1 ]]; then
		echo "kept: ${work}"
	else
		rm -rf -- "${work}"
	fi
}
trap cleanup EXIT
# The image runs as uid 1000 (frappe); the PDFs are written to the mounted directory.
chmod 0777 "${work}"

echo "image ${image}"
status=0
docker run --rm --network none \
	-v "${here}:/checks:ro" -v "${work}:/out" \
	"${image}" env/bin/python /checks/render_tamil.py /checks/tamil-sample.html /out || status=$?
exit "${status}"
