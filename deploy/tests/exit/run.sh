#!/usr/bin/env bash
# deploy/terraform/oci/exit-purge.sh against fake oci and terraform commands: the order of
# the destructive steps, the confirmations, the CERT-In log hold and the records.
#
#   deploy/tests/exit/run.sh          Docker with the image built from deploy/tests/Containerfile
#   H360_EXIT_LOCAL=1 [H360_EXIT_BASH=/bin/bash] deploy/tests/exit/run.sh
#                                     on this machine instead (bash, jq and python3);
#                                     H360_EXIT_BASH=/bin/bash on macOS runs the script
#                                     under bash 3.2, as an administrator's Mac would
set -Eeuo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
deploy="$(cd "${here}/../.." && pwd)"
image="${H360_CHECKS_IMAGE:-hostel360-deploy-checks:1}"

if [[ "${H360_EXIT_LOCAL:-0}" == 1 ]]; then
	H360_DEPLOY_DIR="${deploy}" bash "${here}/checks.sh"
else
	docker run --rm -v "${deploy}:/work/deploy:ro" "${image}" bash /work/deploy/tests/exit/checks.sh
fi
