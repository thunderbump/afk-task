#!/usr/bin/env sh
set -eu

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
case_checkout="${CASE_CHECKOUT:-}"

usage() {
  cat <<'USAGE'
Usage: scripts/apply-case-patches.sh --case-checkout /path/to/workos-case

Applies patches/workos-case/*.patch to an external workos/case checkout.
The checkout can also be supplied with CASE_CHECKOUT=/path/to/workos-case.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --case-checkout)
      if [ "$#" -lt 2 ]; then
        echo "error: --case-checkout requires a path" >&2
        exit 2
      fi
      case_checkout="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -z "$case_checkout" ]; then
  echo "error: set CASE_CHECKOUT or pass --case-checkout /path/to/workos-case" >&2
  exit 2
fi

if ! git -C "$case_checkout" rev-parse --show-toplevel >/dev/null 2>&1; then
  echo "error: Case checkout is not a Git working tree: $case_checkout" >&2
  exit 2
fi

git -C "$case_checkout" am "$repo_root"/patches/workos-case/*.patch
