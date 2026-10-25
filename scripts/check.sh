#!/bin/sh
# The gate: unit tests on the current python3 and on the system Python 3.9
# (macOS /usr/bin/python3), plus the strict plugin validation.
# Ends with RESULT: GREEN or RED. Needs no model and changes nothing.
cd "$(dirname "$0")/.." || exit 1

failed=""
step() {
    name=$1; shift
    echo "== $name"
    if "$@"; then echo "-- $name: ok"; else echo "-- $name: FAILED"; failed="$failed $name"; fi
}

step "tests (python3)" python3 -m unittest discover -q tests
if [ -x /usr/bin/python3 ] && [ "$(command -v python3)" != /usr/bin/python3 ]; then
    step "tests (/usr/bin/python3)" /usr/bin/python3 -m unittest discover -q tests
fi
if command -v claude >/dev/null 2>&1; then
    step "plugin validate" claude plugin validate --strict .
else
    echo "-- plugin validate: skipped (claude not on PATH)"
fi

if [ -n "$failed" ]; then
    echo "RESULT: RED (failed:$failed)"
    exit 1
fi
echo "RESULT: GREEN"
