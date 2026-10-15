#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: export QSYS_CI_REGISTRATION_TOKEN; sudo --preserve-env=QSYS_CI_REGISTRATION_TOKEN $0 --archive FILE --sha256 HEX --repository OWNER/REPO --name NAME" >&2
  exit 2
}

archive=
archive_sha256=
repository=
runner_name=
while (($#)); do
  case "$1" in
    --archive) archive=${2:-}; shift 2 ;;
    --sha256) archive_sha256=${2:-}; shift 2 ;;
    --repository) repository=${2:-}; shift 2 ;;
    --name) runner_name=${2:-}; shift 2 ;;
    *) usage ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "installer must run as root" >&2; exit 1; }
[[ -f $archive ]] || { echo "runner archive does not exist" >&2; exit 1; }
[[ $archive_sha256 =~ ^[0-9a-f]{64}$ ]] || { echo "invalid archive SHA-256" >&2; exit 1; }
[[ $repository =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo "invalid repository" >&2; exit 1; }
[[ $runner_name =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "invalid runner name" >&2; exit 1; }
[[ -n ${QSYS_CI_REGISTRATION_TOKEN:-} ]] || { echo "QSYS_CI_REGISTRATION_TOKEN is required" >&2; exit 1; }
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || {
  echo "only Linux x64 CI hosts are supported" >&2
  exit 1
}

for forbidden in /data/qsys /etc/qsys /opt/qsys/components /home/ubuntu/qsys /home/ubuntu/qsys-test; do
  [[ ! -e $forbidden ]] || { echo "forbidden production path exists: $forbidden" >&2; exit 1; }
done

actual_sha256=$(sha256sum "$archive" | awk '{print $1}')
[[ $actual_sha256 == "$archive_sha256" ]] || { echo "runner archive SHA-256 mismatch" >&2; exit 1; }

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
app_dir=/opt/qsys-ci-runner/app
state_dir=/var/lib/qsys-ci-runner
hook_dir=/usr/local/libexec/qsys-ci-runner
[[ ! -e $app_dir ]] || { echo "$app_dir already exists; rebuild the VM instead of overlaying it" >&2; exit 1; }

if ! id qsys-ci >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$state_dir" --shell /usr/sbin/nologin qsys-ci
fi
install -d -o root -g root -m 0755 /opt/qsys-ci-runner "$hook_dir"
install -d -o qsys-ci -g qsys-ci -m 0700 "$state_dir" "$state_dir/_work"
install -o root -g root -m 0755 "$repo_root/scripts/ci_runner_hook.py" "$hook_dir/ci_runner_hook.py"
install -o root -g root -m 0755 "$repo_root/deploy/ci-runner/job-started.sh" "$hook_dir/job-started.sh"
install -o root -g root -m 0755 "$repo_root/deploy/ci-runner/job-completed.sh" "$hook_dir/job-completed.sh"

install -d -o qsys-ci -g qsys-ci -m 0750 "$app_dir"
tar --extract --gzip --file "$archive" --directory "$app_dir" --no-same-owner --no-same-permissions
ln -s "$state_dir/_work" "$app_dir/_work"
chown -R qsys-ci:qsys-ci "$app_dir"

(
  cd "$app_dir"
  ./bin/installdependencies.sh
  sudo -u qsys-ci -H ./config.sh \
    --unattended \
    --url "https://github.com/$repository" \
    --token "$QSYS_CI_REGISTRATION_TOKEN" \
    --name "$runner_name" \
    --labels qsys-ci \
    --work _work \
    --disableupdate
)
unset QSYS_CI_REGISTRATION_TOKEN

cat >"$app_dir/.env" <<'EOF'
ACTIONS_RUNNER_HOOK_JOB_STARTED=/usr/local/libexec/qsys-ci-runner/job-started.sh
ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/usr/local/libexec/qsys-ci-runner/job-completed.sh
QSYS_CI_WORK_ROOT=/var/lib/qsys-ci-runner/_work
EOF

# Jobs may write only their work and diagnostic directories. Runner credentials,
# application files and hooks remain root-owned and are never workflow inputs.
chown -R root:qsys-ci "$app_dir"
chmod -R o-rwx "$app_dir"
chmod -R g-w "$app_dir"
chmod 0640 "$app_dir/.runner" "$app_dir/.credentials" "$app_dir/.credentials_rsaparams"
chown -R qsys-ci:qsys-ci "$state_dir"
chown qsys-ci:qsys-ci "$app_dir/_diag"
chmod 0750 "$app_dir/_diag"

(
  cd "$app_dir"
  ./svc.sh install qsys-ci
)

echo "runner installed but not started"
echo "after the repository is private, start with: systemctl start actions.runner.*.service"
