from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from qsys.live.messages import Heartbeat
from qsys.live.transport import MemoryTransport
from qsys.web.live_kill_status import LiveKillReader
from qsys.web.app import create_app
from qsys.web.config import WebConfig
from qsys.web.views import MonitorService
from qsys.ops.journal import SessionJournal

NOW = datetime(2026, 10, 20, 14, 0)


def setup_reader(tmp_path, *, engaged=False, age=0, mode='live', field=True):
    transport = MemoryTransport()
    beat = Heartbeat(source='agent', at=NOW-timedelta(seconds=age), trading_mode=mode,
                     local_kill_engaged=engaged if field else None)
    transport.set_key('state:heartbeat:agent', beat.model_dump_json())
    return LiveKillReader(transport, tmp_path / 'KILL'), transport


def test_clear_requires_all_three_sources_and_only_uses_real_live_keys(tmp_path):
    reader, transport = setup_reader(tmp_path)
    transport.set_key('shadow:control:kill', 'unrelated')
    (tmp_path / 'SHADOW_KILL').write_text('unrelated')
    result = reader.read(now=NOW)
    assert result.state == 'clear'
    assert [s.state for s in result.sources] == ['clear'] * 3
    assert all(s.recorded_at is None for s in result.sources[:2])
    assert 9 < result.valid_for_seconds <= 10


@pytest.mark.parametrize('source', ['linux', 'redis', 'agent'])
def test_any_confirmed_stop_wins_even_when_other_sources_are_unknown(tmp_path, source):
    reader, transport = setup_reader(tmp_path, engaged=source == 'agent')
    stamp = '2026-10-20T13:59:00 原始原因 <b>不是页面标记</b>'
    if source == 'linux':
        reader.local_path.write_text(stamp)
        transport.get_key = Mock(side_effect=OSError('private credentials must not escape'))
    elif source == 'redis':
        transport.set_key('control:kill', stamp)
        transport.delete_key('state:heartbeat:agent')
    else:
        reader.local_path = tmp_path / 'missing' / 'KILL'
    result = reader.read(now=NOW)
    assert result.state == 'stopped'
    record = next(s for s in result.sources if s.id == source)
    assert record.state == 'stopped'
    assert record.recorded_at == ('2026-10-20T13:59:00' if source != 'agent' else NOW.isoformat())
    assert 'private credentials' not in str(result.to_dict())


@pytest.mark.parametrize('age,mode,field', [(10,'live',True),(11,'live',True),
    (-2,'live',True),(0,'shadow',True),(0,None,True),(0,'live',False)])
def test_stale_future_non_live_or_missing_agent_posture_is_unknown(tmp_path, age, mode, field):
    reader, _ = setup_reader(tmp_path, age=age, mode=mode, field=field)
    assert reader.read(now=NOW).state == 'unknown'


def test_read_errors_missing_parent_and_unconfigured_paths_never_look_clear(tmp_path, monkeypatch):
    reader, transport = setup_reader(tmp_path)
    reader.local_path = tmp_path / 'missing' / 'KILL'
    assert reader.read(now=NOW).state == 'unknown'
    reader.local_path = None
    assert reader.read(now=NOW).state == 'unknown'
    reader.local_path = tmp_path / 'KILL'
    transport.get_key = Mock(side_effect=OSError('secret'))
    assert reader.read(now=NOW).state == 'unknown'
    reader, _ = setup_reader(tmp_path)
    monkeypatch.setattr(Path, 'lstat', Mock(side_effect=PermissionError('secret')))
    assert reader.read(now=NOW).state == 'unknown'


def test_empty_or_unreadable_existing_flag_still_confirms_stop(tmp_path, monkeypatch):
    reader, _ = setup_reader(tmp_path)
    reader.local_path.touch()
    assert reader.read(now=NOW).state == 'stopped'
    monkeypatch.setattr(Path, 'open', Mock(side_effect=PermissionError('secret')))
    result = reader.read(now=NOW)
    assert result.state == 'stopped'
    assert result.sources[0].reason == '标记存在，原因不可读'


def test_custom_live_path_prefix_and_age_are_respected(tmp_path):
    reader, transport = setup_reader(tmp_path, age=9)
    assert 0 < reader.read(now=NOW).valid_for_seconds <= 1
    reader.heartbeat_prefix = 'live:heartbeat'
    assert reader.read(now=NOW).state == 'unknown'
    transport.set_key('live:heartbeat:agent', transport.get_key('state:heartbeat:agent'))
    reader.local_path = tmp_path / 'CUSTOM_KILL'
    reader.local_path.write_text('原样原因，无时间')
    result = reader.read(now=NOW)
    assert result.state == 'stopped'
    assert result.sources[0].recorded_at is None
    assert result.sources[0].reason == '原样原因，无时间'


def test_api_and_global_navigation_escape_reasons_and_track_live_state(warehouse, tmp_path):
    reader, transport = setup_reader(tmp_path)
    # 使用当前真实测试时刻，不依赖固定日期的老心跳。
    transport.set_key('state:heartbeat:agent', Heartbeat(source='agent',at=datetime.now(),
        trading_mode='live',local_kill_engaged=False).model_dump_json())
    cfg = WebConfig()
    app = create_app(MonitorService(SessionJournal(warehouse), config=cfg), cfg, live_kill=reader)
    client = TestClient(app)
    response = client.get('/api/live/kill-status')
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['state'] == 'clear'
    assert '停机闸已解除' in client.get('/live').text
    reader.local_path.write_text('2026-10-20T14:00:00 <script>alert(1)</script>')
    page = client.get('/live').text
    assert '实盘已停机' in page and '禁止自动开仓' in page
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in page
    assert '<script>alert(1)</script>' not in page
    assert client.get('/api/live/kill-status').json()['state'] == 'stopped'
    reader.local_path.unlink()
    transport.delete_key('state:heartbeat:agent')
    assert client.get('/api/live/kill-status').json()['state'] == 'unknown'


def test_slow_read_cannot_publish_a_fresh_green_snapshot(tmp_path, monkeypatch):
    import qsys.web.live_kill_status as module
    reader, _ = setup_reader(tmp_path)
    times = iter([0.0, 11.0])
    monkeypatch.setattr(module, 'monotonic', lambda: next(times))
    result = reader.read(now=NOW)
    assert result.state == 'unknown'
    assert result.valid_for_seconds == 0


def test_web_component_configures_the_explicit_production_live_flag():
    import json, shlex
    spec = json.loads(Path('deploy/component-releases.json').read_text())['components']['web']
    line = next(l for l in spec['dropin_lines'] if l.startswith('ExecStart=') and l != 'ExecStart=')
    argv = shlex.split(line.removeprefix('ExecStart='))
    assert 'Environment=QSYS_LIVE_KILL_PATH=/home/ubuntu/qsys/var/KILL' in spec['dropin_lines']
    assert '--live-kill-path' not in argv  # 回退到旧 CLI 时不能留下它不认识的参数。
    assert '--live-kill-path' not in line
    assert 'SHADOW_KILL' not in line


@pytest.mark.parametrize('mode', ['live', 'shadow', 'disabled'])
def test_build_app_wires_explicit_path_and_never_reads_shadow_as_live(tmp_path, monkeypatch, mode):
    from qsys.web import monitor_cli
    from qsys.live.config import DeployConfig, SafetyConfig
    import qsys.live.config as live_config
    from qsys.live.redis_transport import RedisTransport
    config = DeployConfig(safety=SafetyConfig(mode=mode))
    transport = MemoryTransport()
    monkeypatch.setattr(live_config, 'load_deploy_config', lambda _: config)
    monkeypatch.setattr(RedisTransport, 'from_config', lambda _: transport)
    captured = {}
    def capture(*args, **kwargs):
        captured.update(kwargs)
        return object()
    monkeypatch.setattr(monitor_cli, 'create_app', capture)
    path = tmp_path / 'explicit-live' / 'CUSTOM_KILL'
    monitor_cli.build_app(tmp_path / 'warehouse', WebConfig(), None,
                          workspace_dir=tmp_path / 'workspace', live_kill_path=path)
    reader = captured['live_kill']
    assert reader.local_path == (path if mode == 'live' else None)
    assert reader.transport is (transport if mode == 'live' else None)


def test_local_live_flag_survives_redis_initialization_failure(tmp_path, monkeypatch):
    from qsys.web import monitor_cli
    from qsys.live.config import DeployConfig, SafetyConfig
    import qsys.live.config as live_config
    from qsys.live.redis_transport import RedisTransport
    monkeypatch.setattr(live_config, 'load_deploy_config',
                        lambda _: DeployConfig(safety=SafetyConfig(mode='live')))
    monkeypatch.setattr(RedisTransport, 'from_config', Mock(side_effect=OSError('offline')))
    captured = {}
    monkeypatch.setattr(monitor_cli, 'create_app', lambda *a, **kw: captured.update(kw))
    path = tmp_path / 'CUSTOM_KILL'
    path.touch()
    monitor_cli.build_app(tmp_path/'warehouse',WebConfig(),None,
                          workspace_dir=tmp_path/'workspace',live_kill_path=path)
    assert captured['live_kill'].read().state == 'stopped'


@pytest.mark.parametrize('override', [False, True])
def test_cli_reads_explicit_environment_and_argument_takes_precedence(tmp_path, monkeypatch, override):
    import sys
    from qsys.web import monitor_cli
    env_path = tmp_path / 'ENV_KILL'
    explicit = tmp_path / 'CLI_KILL'
    monkeypatch.setenv('QSYS_LIVE_KILL_PATH', str(env_path))
    monkeypatch.setattr(sys, 'argv', ['qsys-web'] + (['--live-kill-path', str(explicit)] if override else []))
    captured = []
    monkeypatch.setattr(monitor_cli, 'build_app', lambda *args: captured.append(args) or object())
    monkeypatch.setattr(monitor_cli.uvicorn, 'run', lambda *args, **kwargs: None)
    assert monitor_cli.main() == 0
    assert captured[0][-1] == (explicit if override else env_path)
