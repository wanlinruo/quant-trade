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
