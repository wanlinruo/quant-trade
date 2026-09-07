import pytest

from qsys.live.config import DeployConfig, load_deploy_config

EXAMPLE = """
[redis]
host = "10.0.0.1"
port = 6380
password = "secret"
tls = true
db = 1

[streams]
orders = "cmd:orders"
control = "cmd:control"
fills = "evt:fills"
rejects = "evt:rejects"
account = "state:account"
heartbeat = "state:heartbeat"

[agent]
consumer_group = "core"
consumer_name = "core-1"
"""


def test_loads_all_sections(tmp_path):
    path = tmp_path / "deploy.toml"
    path.write_text(EXAMPLE)
    cfg = load_deploy_config(path)
    assert cfg.redis.host == "10.0.0.1"
    assert cfg.redis.port == 6380
    assert cfg.redis.tls is True
    assert cfg.streams.orders == "cmd:orders"
    assert cfg.consumer_group == "core"


def test_defaults_are_local_and_safe(tmp_path):
    path = tmp_path / "deploy.toml"
    path.write_text("[redis]\nhost = \"127.0.0.1\"\n")
    cfg = load_deploy_config(path)
    assert cfg.redis.port == 6379
    assert cfg.redis.tls is False
    assert cfg.streams.fills == "evt:fills"


def test_missing_file_raises_with_a_helpful_message(tmp_path):
    with pytest.raises(FileNotFoundError, match="deploy.example.toml"):
        load_deploy_config(tmp_path / "nope.toml")


def test_repo_ships_an_example_config():
    from pathlib import Path

    example = Path("config/deploy.example.toml")
    assert example.exists()
    text = example.read_text()
    assert "127.0.0.1" in text, "example 必须只含占位地址"


def test_example_config_is_loadable():
    cfg = load_deploy_config("config/deploy.example.toml")
    assert isinstance(cfg, DeployConfig)


def test_local_config_is_gitignored():
    from pathlib import Path

    assert "config/deploy.local.toml" in Path(".gitignore").read_text()


def test_password_is_not_in_repr(tmp_path):
    """密码不得出现在日志或异常里。"""
    path = tmp_path / "deploy.toml"
    path.write_text(EXAMPLE)
    cfg = load_deploy_config(path)
    assert "secret" not in repr(cfg)
