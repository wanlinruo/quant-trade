"""#601 网关开机自启的守护进程：退出即重启、风暴落本地闸并退避、停止文件、单实例。"""
import json

import pytest

from scripts import run_qmt_gateway_supervisor as sup


class Clock:
    def __init__(self):
        self.t = 1_000.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


def make(tmp_path, codes, *, stop_after=None, **policy):
    clock = Clock()
    spawned = []
    kill = tmp_path / 'run' / 'AGENT_KILL'
    stop = tmp_path / 'run' / 'SUPERVISOR_STOP'

    def spawn(argv):
        spawned.append(argv)
        clock.t += 5                                   # each run lasts 5 s
        if stop_after is not None and len(spawned) >= stop_after:
            stop.parent.mkdir(parents=True, exist_ok=True)
            stop.write_text('maintenance')
        return codes[min(len(spawned) - 1, len(codes) - 1)]

    s = sup.Supervisor(
        gateway_argv=['python', 'run_qmt_agent.py', '--config', 'c.toml'],
        kill_path=kill, stop_path=stop, journal_path=tmp_path / 'run' / 'supervisor.jsonl',
        policy=sup.RestartPolicy(**policy), spawn=spawn, clock=clock.now, sleep=clock.sleep)
    return s, spawned, clock, kill, stop


def journal(tmp_path):
    return [json.loads(x) for x in (tmp_path / 'run' / 'supervisor.jsonl').read_text().splitlines()]


def test_restarts_after_exit_with_normal_delay_until_stop_file(tmp_path):
    s, spawned, clock, kill, _ = make(tmp_path, [1], stop_after=2, delay_s=30)
    assert s.run() == 0
    assert len(spawned) == 2 and spawned[0] == ['python', 'run_qmt_agent.py', '--config', 'c.toml']
    assert not kill.exists()
    events = [e['event'] for e in journal(tmp_path)]
    assert events == ['started', 'gateway_exit', 'gateway_exit', 'stopped']
    assert journal(tmp_path)[1]['code'] == 1 and journal(tmp_path)[1]['delay_s'] == 30


def test_stop_file_present_before_start_spawns_nothing(tmp_path):
    s, spawned, _, _, stop = make(tmp_path, [0])
    stop.parent.mkdir(parents=True)
    stop.write_text('x')
    assert s.run() == 0 and spawned == []


def test_restart_storm_engages_local_kill_once_and_backs_off(tmp_path):
    s, spawned, clock, kill, _ = make(tmp_path, [1], stop_after=5, delay_s=30, window_s=900,
                                      max_exits=3, backoff_s=300)
    s.run()
    assert kill.read_text(encoding='utf-8').startswith('gateway restart storm: 3 exits in 900s')
    exits = [e for e in journal(tmp_path) if e['event'] == 'gateway_exit']
    assert [e['delay_s'] for e in exits] == [30, 30, 300, 300, 300]
    assert [e['storm'] for e in exits] == [False, False, True, True, True]
    assert sum(e['event'] == 'local_kill_engaged' for e in journal(tmp_path)) == 1


def test_existing_kill_reason_is_never_overwritten(tmp_path):
    s, _, _, kill, _ = make(tmp_path, [1], stop_after=4, max_exits=3)
    kill.parent.mkdir(parents=True)
    kill.write_text('remote kill: operator', encoding='utf-8')
    s.run()
    assert kill.read_text(encoding='utf-8') == 'remote kill: operator'


def test_exits_outside_window_do_not_count(tmp_path):
    s, _, clock, kill, _ = make(tmp_path, [1], stop_after=6, delay_s=500, window_s=900,
                                max_exits=3)
    s.run()                                   # one exit every ~505 s: never 3 within 900 s
    assert not kill.exists()


def test_healthy_long_run_resets_storm_window(tmp_path):
    p = sup.RestartPolicy(window_s=900, max_exits=3)
    assert p.record_exit(0.0).storm is False
    assert p.record_exit(10.0).storm is False
    assert p.record_exit(5000.0).storm is False   # first two aged out


def test_second_supervisor_refuses_while_lock_held(tmp_path):
    lock = tmp_path / 'sup.lock'
    with sup.single_instance(lock):
        with pytest.raises(RuntimeError):
            with sup.single_instance(lock):
                pass


def test_cli_requires_gateway_args_after_separator(tmp_path):
    with pytest.raises(SystemExit):
        sup.parse_args(['--config', 'c.toml', '--state-dir', str(tmp_path)])
    args = sup.parse_args(['--config', 'c.toml', '--state-dir', str(tmp_path), '--',
                           '--qmt-private', 'p', '--state-root', 's', '--strategy-id', 'x'])
    assert args.gateway == ['--qmt-private', 'p', '--state-root', 's', '--strategy-id', 'x']


def test_gateway_args_may_not_override_config(tmp_path):
    with pytest.raises(SystemExit):
        sup.parse_args(['--config', 'c.toml', '--state-dir', str(tmp_path), '--',
                        '--config', 'other.toml'])
