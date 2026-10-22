import importlib.util
from pathlib import Path
import subprocess
import re
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location('qsys_github_read', '.github/scripts/github_api_read.py')
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


def response(body=b'{}', error=b'', code=0):
    return subprocess.CompletedProcess([], code, body, error)


@pytest.mark.parametrize('args', [['repos/a/b', '-f', 'x=1'], ['repos/a/b','--input=x'],
    ['repos/a/b','-XPOST'], ['repos/a/b','--method=POST'], ['repos/a/b','-Fx=1']])
def test_never_calls_gh_for_mutation_arguments(args):
    run=Mock()
    with pytest.raises(ValueError):reader.read_api(args,run=run)
    run.assert_not_called()


@pytest.mark.parametrize('first', [response(error=b'unexpected end of JSON input', code=1),
    response(error=b'gh: Not Found (HTTP 404)',code=1),response(body=b''),
    subprocess.TimeoutExpired('gh',40)])
def test_transient_read_retries_without_changing_get(first):
    run=Mock(side_effect=[first,response(b'{"verified":true}')])
    sleep=Mock()
    assert reader.read_api(['repos/a/b'],run=run,sleep=sleep)==b'{"verified":true}'
    assert run.call_count==2
    assert all(c.args[0]==['gh','api','--method','GET','repos/a/b'] for c in run.call_args_list)
    sleep.assert_called_once_with(2)


def test_retry_limit_and_no_sensitive_stderr(capsys):
    run=Mock(return_value=response(error=b'gh: secret-token unexpected end of JSON input',code=1))
    sleep=Mock()
    with pytest.raises(RuntimeError) as e:reader.read_api(['repos/a/b'],run=run,sleep=sleep)
    assert run.call_count==3 and [c.args[0] for c in sleep.call_args_list]==[2,5]
    assert 'secret-token' not in str(e.value)+capsys.readouterr().err


def test_auth_denial_is_not_retried():
    run=Mock(return_value=response(error=b'HTTP 403',code=1));sleep=Mock()
    with pytest.raises(RuntimeError,match='HTTP 403'):reader.read_api(['repos/a/b'],run=run,sleep=sleep)
    assert run.call_count==1
    sleep.assert_not_called()


def test_jq_scalar_is_preserved():
    run=Mock(return_value=response(b'success\n'))
    assert reader.read_api(['repos/a/b','--jq','.state'],run=run)==b'success\n'


def test_retry_helper_is_used_only_in_read_gate_steps():
    for name in ['deploy-production-component.yml','deploy-production-linux.yml','align-test-after-main.yml']:
        text=Path('.github/workflows',name).read_text()
        blocks=re.split(r'\n      - name:', text)
        helper_steps=[block for block in blocks if 'github_api_read.py' in block]
        assert helper_steps
        for block in helper_steps:
            assert '--method POST' not in block and '-X POST' not in block
    assert 'gh api --method POST' in Path('.github/workflows/deploy-production-component.yml').read_text()
