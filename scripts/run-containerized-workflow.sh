#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
engine="${CONTAINER_ENGINE:-}"
image="${WORKFLOW_RUNNER_IMAGE:-automation-simple-workflow-case-runner:local}"
build_image=0
case_setup_mode="auto"
volume_suffix="${WORKFLOW_VOLUME_SUFFIX:-:rw}"
readonly_volume_suffix="${WORKFLOW_READONLY_VOLUME_SUFFIX:-:ro}"
declare -a extra_mounts=()
declare -a readonly_mounts=()

usage() {
  cat <<'USAGE'
Usage: scripts/run-containerized-workflow.sh [options] -- run --bead <id> [workflow args]

Runs the workflow inside a container while letting Case own its normal pipeline.

Options:
  --build              Build the local runner image before running.
  --engine NAME        Container engine to use. Defaults to podman, then docker.
  --image NAME         Runner image tag. Default: automation-simple-workflow-case-runner:local.
  --repo-root PATH     Workflow checkout to mount. Defaults to this repository.
  --mount PATH         Extra host path to mount at the same absolute path. Repeatable.
  --mount-ro PATH      Extra read-only host path to mount at the same absolute path. Repeatable.
  --volume-suffix S    Suffix for read-write mounts. Default: :rw. Use :Z or :z on SELinux hosts.
  --ro-volume-suffix S Suffix for read-only mounts. Default: :ro. Use :ro,Z or :ro,z on SELinux hosts.
  --setup-case         Refresh the Case checkout inside the container before running.
  --skip-case-setup    Do not create or refresh the Case checkout in the container.
  -h, --help           Show this help.

Everything after -- is passed to `python3 -m automation_simple_workflow`.
USAGE
}

absolute_path() {
  local path="$1"
  local parent name
  parent="$(dirname -- "$path")"
  name="$(basename -- "$path")"
  mkdir -p "$parent"
  printf "%s/%s" "$(cd "$parent" && pwd)" "$name"
}

detect_engine() {
  if [[ -n "$engine" ]]; then
    return
  fi
  if command -v podman >/dev/null 2>&1; then
    engine="podman"
  elif command -v docker >/dev/null 2>&1; then
    engine="docker"
  else
    echo "error: no container engine found; install podman or docker" >&2
    exit 2
  fi
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --build)
      build_image=1
      shift
      ;;
    --engine)
      if [[ "$#" -lt 2 ]]; then
        echo "error: --engine requires a value" >&2
        exit 2
      fi
      engine="$2"
      shift 2
      ;;
    --image)
      if [[ "$#" -lt 2 ]]; then
        echo "error: --image requires a value" >&2
        exit 2
      fi
      image="$2"
      shift 2
      ;;
    --repo-root)
      if [[ "$#" -lt 2 ]]; then
        echo "error: --repo-root requires a path" >&2
        exit 2
      fi
      repo_root="$(absolute_path "$2")"
      shift 2
      ;;
    --mount)
      if [[ "$#" -lt 2 ]]; then
        echo "error: --mount requires a path" >&2
        exit 2
      fi
      extra_mounts+=("$(absolute_path "$2")")
      shift 2
      ;;
    --mount-ro)
      if [[ "$#" -lt 2 ]]; then
        echo "error: --mount-ro requires a path" >&2
        exit 2
      fi
      readonly_mounts+=("$(absolute_path "$2")")
      shift 2
      ;;
    --volume-suffix)
      if [[ "$#" -lt 2 ]]; then
        echo "error: --volume-suffix requires a value" >&2
        exit 2
      fi
      volume_suffix="$2"
      shift 2
      ;;
    --ro-volume-suffix)
      if [[ "$#" -lt 2 ]]; then
        echo "error: --ro-volume-suffix requires a value" >&2
        exit 2
      fi
      readonly_volume_suffix="$2"
      shift 2
      ;;
    --setup-case)
      case_setup_mode="always"
      shift
      ;;
    --skip-case-setup)
      case_setup_mode="0"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    *)
      echo "error: unknown argument before --: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$#" -eq 0 ]]; then
  echo "error: missing workflow command after --" >&2
  usage >&2
  exit 2
fi

detect_engine

if [[ "$build_image" -eq 1 ]]; then
  "$engine" build \
    -f "$repo_root/containers/case-runner/Containerfile" \
    -t "$image" \
    "$repo_root"
fi

declare -a engine_user_args=()
case "$(basename -- "$engine")" in
  podman)
    engine_user_args=(--userns=keep-id)
    ;;
  docker)
    engine_user_args=(--user "$(id -u):$(id -g)")
    ;;
esac

declare -a mount_args=(
  --volume "$repo_root:$repo_root${volume_suffix}"
)

for mount_path in "${extra_mounts[@]}"; do
  mount_args+=(--volume "$mount_path:$mount_path${volume_suffix}")
done

for mount_path in "${readonly_mounts[@]}"; do
  mount_args+=(--volume "$mount_path:$mount_path${readonly_volume_suffix}")
done

case_checkout="${CASE_CHECKOUT:-$repo_root/.external/workos-case}"

exec "$engine" run --rm \
  "${engine_user_args[@]}" \
  "${mount_args[@]}" \
  --workdir "$repo_root" \
  --env "CASE_CHECKOUT=$case_checkout" \
  --env "WORKFLOW_SETUP_CASE=$case_setup_mode" \
  "$image" \
  "$@"
