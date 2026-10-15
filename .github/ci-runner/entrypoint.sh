#!/usr/bin/env bash
set -euo pipefail

jit_file=${QSYS_CI_JIT_FILE:-/run/qsys-ci/jit-config}
[[ -r $jit_file ]] || { echo "JIT configuration is unavailable" >&2; exit 1; }
jit_config=$(<"$jit_file")
[[ -n $jit_config && $jit_config != *[[:space:]]* ]] || {
  echo "invalid JIT configuration" >&2
  exit 1
}
exec ./run.sh --jitconfig "$jit_config"
