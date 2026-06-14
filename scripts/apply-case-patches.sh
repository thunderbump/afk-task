#!/usr/bin/env sh
set -eu

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
case_checkout="${CASE_CHECKOUT:-/home/bump/Projects/automation/.automation/cache/workos-case}"

git -C "$case_checkout" am "$repo_root"/patches/workos-case/*.patch
