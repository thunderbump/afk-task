#!/usr/bin/env sh
set -eu

case_checkout="${CASE_CHECKOUT:-$(pwd)/.external/workos-case}"
setup_mode="${WORKFLOW_SETUP_CASE:-auto}"

if [ "$#" -eq 0 ]; then
  exec python3 -m automation_simple_workflow --help
fi

if [ "$setup_mode" = "always" ]; then
  scripts/setup-case-checkout.sh --case-checkout "$case_checkout"
elif [ "$setup_mode" != "0" ] && [ ! -d "$case_checkout/.git" ]; then
  scripts/setup-case-checkout.sh --case-checkout "$case_checkout"
fi

export CASE_CHECKOUT="$case_checkout"
exec python3 -m automation_simple_workflow "$@"
