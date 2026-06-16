#!/usr/bin/env sh
set -eu

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
case_checkout="${CASE_CHECKOUT:-}"
patch_dir="$repo_root/patches/workos-case"

usage() {
  cat <<'USAGE'
Usage: scripts/apply-case-patches.sh --case-checkout /path/to/workos-case [--patch-dir /path/to/patches]

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
    --patch-dir)
      if [ "$#" -lt 2 ]; then
        echo "error: --patch-dir requires a path" >&2
        exit 2
      fi
      patch_dir="$2"
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

if [ ! -d "$patch_dir" ]; then
  echo "error: patch directory does not exist: $patch_dir" >&2
  exit 2
fi

set -- "$patch_dir"/*.patch
if [ ! -e "$1" ]; then
  echo "error: no patch files found in: $patch_dir" >&2
  exit 2
fi

applied_patch_ids="$(git -C "$case_checkout" log -p --reverse --format=email | git patch-id --stable 2>/dev/null | awk '{print $1}' || true)"

for patch in "$@"; do
  patch_id="$(git patch-id --stable < "$patch" 2>/dev/null | awk 'NR == 1 {print $1}' || true)"
  subject="$(
    sed -n 's/^Subject: //p' "$patch" |
      sed -n '1{s/^\[[^]]*\] //; p;}'
  )"
  if [ -n "$patch_id" ] && printf "%s\n" "$applied_patch_ids" | grep -F -x -- "$patch_id" >/dev/null 2>&1; then
    echo "already applied: $(basename -- "$patch")"
  elif [ -n "$subject" ] && git -C "$case_checkout" log --format=%s | grep -F -x -- "$subject" >/dev/null 2>&1; then
    echo "already applied by subject: $(basename -- "$patch")"
  elif git -C "$case_checkout" apply --reverse --check "$patch" >/dev/null 2>&1; then
    echo "already applied: $(basename -- "$patch")"
  else
    git -C "$case_checkout" am "$patch"
    if [ -n "$patch_id" ]; then
      applied_patch_ids="${applied_patch_ids}
${patch_id}"
    fi
  fi
done
