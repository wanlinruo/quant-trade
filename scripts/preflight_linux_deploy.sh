#!/bin/sh
set -eu

repo=$(git rev-parse --show-toplevel)
cd "$repo"

if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
    echo "refusing deployment preflight in a dirty worktree: $repo" >&2
    exit 1
fi

uv_bin=${QSYS_UV_BIN:-/home/ubuntu/.local/bin/uv}

# Linux 节点用腾讯镜像下载，但仓库里的 lock 由 CI 的官方 PyPI 配置生成。
# 裸 `uv run` 会在执行命令前重新锁定，并把全部 registry URL 改写成节点镜像；
# 因此 sync 和每一次 run 都必须显式 frozen。
"$uv_bin" sync --frozen
"$uv_bin" run --frozen python -c \
    "import qsys.agent.stall_guard; import scripts.check_agent_alive; import scripts.run_agent"
"$uv_bin" run --frozen pytest -q tests/scripts/test_scripts_import.py

if ! git diff --quiet -- uv.lock; then
    echo "deployment preflight modified uv.lock" >&2
    exit 1
fi
if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
    echo "deployment preflight left a dirty worktree: $repo" >&2
    exit 1
fi

echo "linux deployment preflight ok: $(git rev-parse HEAD)"
