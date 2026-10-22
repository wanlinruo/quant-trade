from __future__ import annotations

import hashlib
import io
import json
import subprocess

import pytest

from qsys.data.windows_data_transport import WindowsDataTransport


def transport(monkeypatch, contents, *, name='minutes_00000.parquet', corrupt=False):
    client=WindowsDataTransport('Administrator@example','/private/key',component='tushare-sync',known_hosts='/private/known_hosts')
    inventory={'files':[{'name':name,'size':len(contents),'sha256':hashlib.sha256(contents).hexdigest()}]}
    monkeypatch.setattr(client,'_json',lambda *a,**kw:inventory)
    def get(args,**kw):
        kw['stdout'].write(b'wrong' if corrupt else contents)
        return subprocess.CompletedProcess(args,0)
    monkeypatch.setattr(subprocess,'run',get)
    return client


def test_received_results_are_hash_verified_and_existing_parquet_never_overwritten(monkeypatch,tmp_path):
    client=transport(monkeypatch,b'bytes')
    assert client.receive('minute_20261021',tmp_path)==1
    target=tmp_path/'minutes_00000.parquet'
    assert target.read_bytes()==b'bytes'
    client=transport(monkeypatch,b'changed')
    with pytest.raises(ValueError,match='differs'):client.receive('minute_20261021',tmp_path)
    assert target.read_bytes()==b'bytes'


def test_bad_transfer_never_publishes_a_partial_result(monkeypatch,tmp_path):
    client=transport(monkeypatch,b'correct',corrupt=True)
    with pytest.raises(ValueError,match='mismatch'):client.receive('minute_20261021',tmp_path)
    assert not list(tmp_path.iterdir())


def test_updated_progress_preserves_original_metadata_before_replace(monkeypatch,tmp_path):
    target=tmp_path/'result.json';target.write_bytes(b'old-result')
    client=transport(monkeypatch,b'new-result',name='result.json')
    client.receive('minute_20261021',tmp_path)
    assert target.read_bytes()==b'new-result'
    assert next(tmp_path.glob('.previous-*')).read_bytes()==b'old-result'


@pytest.mark.parametrize('name',['../private.json','C:/private.json','..\\private.json'])
def test_output_inventory_cannot_escape_namespace(monkeypatch,tmp_path,name):
    client=transport(monkeypatch,b'bytes',name=name)
    with pytest.raises(ValueError,match='unsafe'):client.receive('minute_20261021',tmp_path)
    assert not list(tmp_path.iterdir())


def test_probe_refuses_wrong_component_or_protocol(monkeypatch):
    client=WindowsDataTransport('host','key',component='tushare-sync',known_hosts='/private/known_hosts')
    monkeypatch.setattr(client,'_json',lambda *a,**kw:{'protocol':1,'component':'daily-archive'})
    with pytest.raises(ValueError,match='mismatch'):client.probe()


def test_private_key_and_host_key_are_explicit_and_ssh_config_cannot_override_them():
    client=WindowsDataTransport('Administrator@example','key',component='tushare-sync',known_hosts='pinned')
    args=client.command('probe')
    assert args[:3]==['ssh','-F','/dev/null']
    assert 'IdentitiesOnly=yes' in args
    assert 'StrictHostKeyChecking=yes' in args
    assert 'UserKnownHostsFile=pinned' in args
    with pytest.raises(ValueError,match='known_hosts'):
        WindowsDataTransport('host','key',component='tushare-sync',known_hosts='')


PREAUTH = b'kex_exchange_identification: read: Connection reset by peer\r\nConnection reset by 192.0.2.1 port 22\r\n'


@pytest.mark.parametrize('kind', ['json', 'download', 'get'])
def test_only_preauth_reset_retries_then_succeeds(monkeypatch, tmp_path, kind):
    from types import SimpleNamespace
    from qsys.data import windows_data_transport as module
    client = WindowsDataTransport('host', 'key', component='tushare-sync', known_hosts='pinned')
    sleeps = []
    calls = []
    monkeypatch.setattr(module.time, 'sleep', sleeps.append)
    def execute(command, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise subprocess.CalledProcessError(255, command, output=b'', stderr=PREAUTH)
        if 'stdout' in kwargs:
            kwargs['stdout'].write(b'complete')
        return subprocess.CompletedProcess(command, 0, stdout=b'{"ok":true}')
    monkeypatch.setattr(module.subprocess, 'run', execute)
    if kind == 'json':
        assert client._json('probe') == {'ok': True}
    elif kind == 'get':
        with (tmp_path/'download').open('w+b') as stream:
            client._run_ssh(['ssh'], stdout=stream, check=True, timeout=120)
            stream.seek(0)
            assert stream.read() == b'complete'
    else:
        def runner(command, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(returncode=255 if len(calls)==1 else 0,
                                   output=PREAUTH.decode() if len(calls)==1 else 'business progress', error='')
        assert client.run_download(['ssh'], runner=runner, timeout=120).returncode == 0
    assert len(calls) == 2 and sleeps == [2]


@pytest.mark.parametrize('output,error', [
    ('Connection reset by peer', ''),
    ('Permission denied (publickey,password).', ''),
    ('progress started\n'+PREAUTH.decode(), ''),
    (PREAUTH.decode()+'remote command started\n', ''),
    (PREAUTH.decode(), 'download timeout'),
])
def test_stream_execution_authentication_or_timeout_errors_are_never_replayed(monkeypatch, output, error):
    from types import SimpleNamespace
    from qsys.data import windows_data_transport as module
    client = WindowsDataTransport('host', 'key', component='tushare-sync', known_hosts='pinned')
    monkeypatch.setattr(module.time, 'sleep', lambda *a: pytest.fail('unsafe retry'))
    expected = SimpleNamespace(returncode=255, output=output, error=error)
    calls = []
    assert client.run_download(['ssh'], runner=lambda *a,**kw: calls.append(kw) or expected, timeout=120) is expected
    assert len(calls) == 1


def test_preauth_retry_limit_is_three_attempts(monkeypatch):
    from types import SimpleNamespace
    from qsys.data import windows_data_transport as module
    client = WindowsDataTransport('host', 'key', component='tushare-sync', known_hosts='pinned')
    sleeps=[];calls=[]
    monkeypatch.setattr(module.time, 'sleep', sleeps.append)
    result = SimpleNamespace(returncode=255, output=PREAUTH.decode(), error='')
    assert client.run_download(['ssh'], runner=lambda *a,**kw:calls.append(kw) or result, timeout=120) is result
    assert len(calls)==3 and sleeps==[2,5]


@pytest.mark.parametrize('failure', ['partial', 'timeout', 'auth', 'session-reset'])
def test_rpc_and_file_receive_do_not_replay_after_possible_execution(monkeypatch, tmp_path, failure):
    from qsys.data import windows_data_transport as module
    client = WindowsDataTransport('host', 'key', component='tushare-sync', known_hosts='pinned')
    monkeypatch.setattr(module.time, 'sleep', lambda *a: pytest.fail('unsafe retry'))
    calls=[]
    def execute(command, **kwargs):
        calls.append(kwargs)
        if failure == 'partial':
            kwargs['stdout'].write(b'partial bytes')
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(command, 120)
        stderr = PREAUTH if failure=='partial' else b'Permission denied' if failure=='auth' else b'Connection reset by peer'
        raise subprocess.CalledProcessError(255, command, stderr=stderr)
    monkeypatch.setattr(module.subprocess, 'run', execute)
    with (tmp_path/'partial').open('w+b') as stream, pytest.raises((subprocess.CalledProcessError,subprocess.TimeoutExpired)):
        client._run_ssh(['ssh'], stdout=stream, check=True, timeout=120)
    assert len(calls)==1


@pytest.mark.parametrize('kind', ['json', 'get'])
def test_rpc_preauth_limit_remains_bounded(monkeypatch, tmp_path, kind):
    from qsys.data import windows_data_transport as module
    client = WindowsDataTransport('host','key',component='tushare-sync',known_hosts='pinned')
    calls=[];sleeps=[]
    monkeypatch.setattr(module.time,'sleep',sleeps.append)
    def rejected(command,**kwargs):
        calls.append(kwargs)
        raise subprocess.CalledProcessError(255,command,output=b'',stderr=PREAUTH)
    monkeypatch.setattr(module.subprocess,'run',rejected)
    with pytest.raises(subprocess.CalledProcessError):
        if kind=='json': client._json('probe')
        else:
            with (tmp_path/'output').open('w+b') as output:
                client._run_ssh(['ssh'],stdout=output,timeout=120,check=True)
    assert len(calls)==3 and sleeps==[2,5]


def test_stream_retry_does_not_reset_total_timeout_budget(monkeypatch):
    from types import SimpleNamespace
    from qsys.data import windows_data_transport as module
    client=WindowsDataTransport('host','key',component='tushare-sync',known_hosts='pinned')
    clock=[0.0];calls=[]
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(module.time,'sleep',lambda *a:pytest.fail('remaining budget exhausted'))
    result=SimpleNamespace(returncode=255,output=PREAUTH.decode(),error='')
    def runner(*a,**kw):
        calls.append(kw);clock[0]=119.0;return result
    assert client.run_download(['ssh'],runner=runner,timeout=120) is result
    assert len(calls)==1


def test_connection_socket_is_private_per_client_and_reused_for_every_operation(monkeypatch):
    from pathlib import Path
    from qsys.data import windows_data_transport as module
    a=WindowsDataTransport('host','key',component='tushare-sync',known_hosts='pinned')
    b=WindowsDataTransport('host','key',component='daily-archive',known_hosts='pinned')
    def control(command):
        return Path(next(x.split('=',1)[1] for x in command if x.startswith('ControlPath=')))
    first=a.command('probe');second=a.command('get',job='minute_20261022',file='result.json')
    path=control(first)
    assert path==control(second) and path!=control(b.command('probe'))
    assert path.parent.stat().st_mode & 0o077 == 0
    assert 'ControlMaster=auto' in first and 'ControlPersist=30' in first
    assert 'StrictHostKeyChecking=yes' in first and first[-2]=='host'
    closed=[]
    monkeypatch.setattr(module.subprocess,'run',lambda command,**kw:closed.append((command,kw)) or subprocess.CompletedProcess(command,0))
    a.close();a.close();b.close()
    assert len(closed)==2 and closed[0][0][-5:]==['-S',str(path),'-O','exit','host']
    assert not path.parent.exists()


def test_gateway_context_closes_authenticated_connection_after_failure(monkeypatch):
    from qsys.data import windows_data_transport as module
    closed=[]
    monkeypatch.setattr(module.subprocess,'run',lambda command,**kw:closed.append(command) or subprocess.CompletedProcess(command,0))
    with pytest.raises(ValueError,match='business failure'):
        with WindowsDataTransport('host','key',component='tushare-sync',known_hosts='pinned') as client:
            client.command('probe')
            root=client._control_dir
            raise ValueError('business failure')
    assert len(closed)==1 and not root.exists()
