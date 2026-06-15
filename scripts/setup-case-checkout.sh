#!/usr/bin/env sh
set -eu

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
case_checkout="${CASE_CHECKOUT:-$repo_root/.external/workos-case}"
case_repo="${CASE_REPO:-https://github.com/workos/case.git}"
patch_dir="$repo_root/patches/workos-case"
run_install=1
run_generate_assets=1

usage() {
  cat <<'USAGE'
Usage: scripts/setup-case-checkout.sh [options]

Clones and prepares an external workos/case checkout for the workflow.

Options:
  --case-checkout PATH  Checkout path. Defaults to CASE_CHECKOUT or .external/workos-case.
  --case-repo URL       Case repository URL. Defaults to CASE_REPO or https://github.com/workos/case.git.
  --patch-dir PATH      Patch directory. Defaults to patches/workos-case.
  --skip-install        Do not run bun install.
  --skip-generate       Do not run bun run generate:assets.
  -h, --help            Show this help.
USAGE
}

quote_for_export() {
  printf "%s" "$1" | sed "s/'/'\\\\''/g"
}

absolute_checkout_path() {
  parent="$(dirname -- "$case_checkout")"
  name="$(basename -- "$case_checkout")"
  mkdir -p "$parent"
  parent_abs="$(CDPATH= cd -- "$parent" && pwd)"
  case_checkout="$parent_abs/$name"
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
    --case-repo)
      if [ "$#" -lt 2 ]; then
        echo "error: --case-repo requires a URL or path" >&2
        exit 2
      fi
      case_repo="$2"
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
    --skip-install)
      run_install=0
      shift
      ;;
    --skip-generate)
      run_generate_assets=0
      shift
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

absolute_checkout_path

if [ ! -e "$case_checkout/.git" ]; then
  echo "Cloning Case into $case_checkout"
  git clone "$case_repo" "$case_checkout"
elif git -C "$case_checkout" rev-parse --show-toplevel >/dev/null 2>&1; then
  echo "Using existing Case checkout at $case_checkout"
else
  echo "error: path exists but is not a Git checkout: $case_checkout" >&2
  exit 2
fi

if [ -z "$(git -C "$case_checkout" config user.email || true)" ]; then
  git -C "$case_checkout" config user.email "agent@example.invalid"
fi

if [ -z "$(git -C "$case_checkout" config user.name || true)" ]; then
  git -C "$case_checkout" config user.name "Automation Workflow"
fi

if [ "$run_install" -eq 1 ]; then
  if ! command -v bun >/dev/null 2>&1; then
    echo "error: bun is required to install Case dependencies" >&2
    exit 2
  fi
  (cd "$case_checkout" && bun install)
fi

"$repo_root/scripts/apply-case-patches.sh" \
  --case-checkout "$case_checkout" \
  --patch-dir "$patch_dir"

if [ "$run_generate_assets" -eq 1 ]; then
  if ! command -v bun >/dev/null 2>&1; then
    echo "error: bun is required to regenerate Case assets" >&2
    exit 2
  fi
  (cd "$case_checkout" && bun run generate:assets)
fi

echo "Case checkout ready: $case_checkout"
printf "export CASE_CHECKOUT='%s'\n" "$(quote_for_export "$case_checkout")"
