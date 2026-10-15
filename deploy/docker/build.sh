#!/usr/bin/env bash
# Build the Hostel360 application image.
#
#   deploy/docker/build.sh                         local image for this machine's platform
#   deploy/docker/build.sh --push                  linux/arm64 + linux/amd64, pushed to OCIR
#   deploy/docker/build.sh --push --platform linux/arm64
#
# Registry: OCIR_REGION and OCIR_NAMESPACE (or --image) select
#   <region>.ocir.io/<namespace>/hostel360
# Log in first:  docker login <region>.ocir.io -u '<namespace>/<user>'   (auth token as password)
#
# Tags: <version>-<git sha> (immutable, what deployments use) and <version>.
# A tree with uncommitted or untracked files gets a "-dirty" suffix and is refused with
# --push, so every pushed image can be rebuilt from its commit.
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/../.." && pwd)"

push=0
platforms=""
image="${H360_IMAGE:-}"
extra_tags=()
builder="${H360_BUILDER:-h360-builder}"
frappe_version="${FRAPPE_VERSION:-v16.52.0}"
frappe_build_image="${FRAPPE_BUILD_IMAGE:-frappe/build:v16.51.0}"
frappe_base_image="${FRAPPE_BASE_IMAGE:-frappe/base:v16.52.0}"

usage() {
	sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
	cat <<'EOF'

Options:
  --push               build for all platforms and push to the registry
  --platform LIST      comma-separated platforms (default: native, or arm64+amd64 with --push)
  --image NAME         full image name without tag (overrides OCIR_REGION/OCIR_NAMESPACE)
  --tag TAG            additional tag (repeatable)
  -h, --help           show this help
EOF
}

die() {
	echo "build.sh: $*" >&2
	exit 1
}

while [[ $# -gt 0 ]]; do
	case "$1" in
		--push) push=1 ;;
		--platform)
			platforms="${2:?--platform needs a value}"
			shift
			;;
		--image)
			image="${2:?--image needs a value}"
			shift
			;;
		--tag)
			extra_tags+=("${2:?--tag needs a value}")
			shift
			;;
		-h | --help)
			usage
			exit 0
			;;
		*) die "unknown option: $1 (see --help)" ;;
	esac
	shift
done

command -v docker >/dev/null || die "docker is not installed"
docker buildx version >/dev/null 2>&1 || die "docker buildx is required"

if [[ -z "${image}" ]]; then
	if [[ -n "${OCIR_REGION:-}" && -n "${OCIR_NAMESPACE:-}" ]]; then
		image="${OCIR_REGION}.ocir.io/${OCIR_NAMESPACE}/hostel360"
	elif [[ "${push}" == 1 ]]; then
		die "set OCIR_REGION and OCIR_NAMESPACE (or --image) to push"
	else
		image="hostel360"
	fi
fi

version="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "${repo_root}/hostel360/__init__.py")"
[[ -n "${version}" ]] || die "could not read __version__ from hostel360/__init__.py"

vcs_ref="unknown"
if git -C "${repo_root}" rev-parse --git-dir >/dev/null 2>&1; then
	vcs_ref="$(git -C "${repo_root}" rev-parse --short=12 HEAD)"
	if [[ -n "$(git -C "${repo_root}" status --porcelain)" ]]; then
		[[ "${push}" == 1 ]] && die "refusing to push an image built from uncommitted changes"
		vcs_ref="${vcs_ref}-dirty"
	fi
fi
build_date="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

tags=("${image}:${version}-${vcs_ref}" "${image}:${version}")
for tag in "${extra_tags[@]}"; do
	[[ "${tag}" == "latest" ]] && die "the latest tag is not used; deployments pin an exact tag"
	tags+=("${image}:${tag}")
done

if [[ -z "${platforms}" ]]; then
	if [[ "${push}" == 1 ]]; then
		platforms="linux/arm64,linux/amd64"
	else
		case "$(uname -m)" in
			arm64 | aarch64) platforms="linux/arm64" ;;
			*) platforms="linux/amd64" ;;
		esac
	fi
fi

args=(
	--file "${script_dir}/Containerfile"
	--platform "${platforms}"
	--build-arg "FRAPPE_VERSION=${frappe_version}"
	--build-arg "FRAPPE_BUILD_IMAGE=${frappe_build_image}"
	--build-arg "FRAPPE_BASE_IMAGE=${frappe_base_image}"
	--build-arg "VERSION=${version}"
	--build-arg "VCS_REF=${vcs_ref}"
	--build-arg "BUILD_DATE=${build_date}"
)
for tag in "${tags[@]}"; do
	args+=(--tag "${tag}")
done

if [[ "${push}" == 1 || "${platforms}" == *,* ]]; then
	# Multi-platform builds need a container builder (QEMU emulates the foreign platform).
	if ! docker buildx inspect "${builder}" >/dev/null 2>&1; then
		docker buildx create --name "${builder}" --driver docker-container --bootstrap >/dev/null
	fi
	args+=(--builder "${builder}")
fi

if [[ "${push}" == 1 ]]; then
	args+=(--push --provenance=mode=max --sbom=true)
elif [[ "${platforms}" == *,* ]]; then
	die "a multi-platform image cannot be loaded locally; add --push or pass a single --platform"
else
	args+=(--load)
fi

echo "Building ${tags[0]} for ${platforms} (Frappe ${frappe_version})" >&2
docker buildx build "${args[@]}" "${repo_root}"

echo >&2
echo "Built:" >&2
printf '  %s\n' "${tags[@]}" >&2
if [[ "${push}" == 1 ]]; then
	echo "Deploy on the server with:  sudo h360-deploy ${version}-${vcs_ref}" >&2
fi
