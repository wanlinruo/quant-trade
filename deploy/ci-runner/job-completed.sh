#!/usr/bin/env bash
set -euo pipefail

exec /usr/bin/python3 /usr/local/libexec/qsys-ci-runner/ci_runner_hook.py cleanup
