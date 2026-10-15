import importlib.util
from pathlib import Path

from test_ci_runner_gate import GATE  # registers runner_gate for controller import


CONTROLLER_PATH = Path(".github/ci-runner/runner_controller.py")
CONTROLLER_SPEC = importlib.util.spec_from_file_location("runner_controller", CONTROLLER_PATH)
assert CONTROLLER_SPEC is not None and CONTROLLER_SPEC.loader is not None
CONTROLLER = importlib.util.module_from_spec(CONTROLLER_SPEC)
CONTROLLER_SPEC.loader.exec_module(CONTROLLER)


def test_jit_generation_requires_private_repository(monkeypatch):
    monkeypatch.setattr(CONTROLLER, "github_json", lambda *args, **kwargs: {"private": False})
    try:
        CONTROLLER.generate_jit_config("owner/repo", 1, "token", "runner")
    except RuntimeError as error:
        assert "must be private" in str(error)
    else:
        raise AssertionError("public repository was accepted")


def test_jit_generation_uses_one_job_labels(monkeypatch):
    calls = []

    def fake(url, token, *, body=None):
        calls.append((url, token, body))
        if body is None:
            return {"private": True}
        return {"encoded_jit_config": "opaque-config_123="}

    monkeypatch.setattr(CONTROLLER, "github_json", fake)
    assert CONTROLLER.generate_jit_config("owner/repo", 1, "secret", "runner-1") == "opaque-config_123="
    assert calls[1][2] == {
        "name": "runner-1",
        "runner_group_id": 1,
        "labels": ["self-hosted", "linux", "x64", "qsys-ci"],
        "work_folder": "_work",
    }


def test_podman_command_has_no_production_mount_or_socket(tmp_path):
    command = CONTROLLER.podman_command(
        uid=999,
        gid=998,
        state=tmp_path,
        image_id="sha256:" + "a" * 64,
        jit_file=tmp_path / "jit",
    )
    rendered = " ".join(map(str, command))
    assert "--rm" in command
    assert "--cap-drop=all" in command
    assert "--security-opt=no-new-privileges" in command
    assert "allow_host_loopback=false" in rendered
    assert "--memory=2300m" in command
    assert "/data/qsys" not in rendered
    assert "/var/run/docker.sock" not in rendered
    assert rendered.count("--volume") == 1


def test_controller_assets_remain_default_disabled():
    installer = Path(".github/ci-runner/install.sh").read_text()
    containerfile = Path(".github/ci-runner/Containerfile").read_text()
    service = Path(".github/ci-runner/qsys-ci-runner.service").read_text()
    assert containerfile.startswith(
        "FROM dockerproxy.net/library/ubuntu@sha256:"
        "f610ab94648195aa356059f5b41d6085c9d4d903c072430cdd1af7bdb646106b\n"
    )
    assert "systemctl disable --now qsys-ci-runner.timer" in installer
    assert "systemctl disable --now \"${rootful_podman_units[@]}\"" in installer
    assert "systemctl mask \"${rootful_podman_units[@]}\"" in installer
    assert 'env HOME="$state" USER=qsys-ci LOGNAME=qsys-ci' in installer
    assert 'cd "$state"' in installer
    assert 'mktemp -d --tmpdir="$state" build-context.XXXXXXXX' in installer
    assert 'cp -a "$repo_root/.github/ci-runner/." "$build_context/"' in installer
    assert 'chown -R qsys-ci:qsys-ci "$build_context"' in installer
    assert '--file "$build_context/Containerfile"' in installer
    assert "ConditionPathExists=/etc/qsys/ci-runner/enabled" in service
    assert "MemoryMax=2500M" in service
    assert "Delegate=cpu cpuset io memory pids" in service


def test_network_policy_denies_host_and_private_networks_but_keeps_dns():
    policy = Path(".github/ci-runner/qsys-ci-runner.nft.in").read_text()
    assert "127.0.0.53" in policy and "th dport 53 accept" in policy
    assert "10.0.0.0/8" in policy
    assert "169.254.0.0/16" in policy
    assert "@@HOST_ADDRESS@@" in policy
    assert "@@RUNNER_UID@@" in policy
