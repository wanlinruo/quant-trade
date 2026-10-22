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


SHA = 'a' * 40


def ci(run_id=10, **changes):
    return {'id':run_id,'head_sha':SHA,'head_branch':'test','event':'push',
            'status':'completed','conclusion':'success',**changes}


def pages(*runs):
    import json
    return json.dumps({'workflow_runs':list(runs)}).encode()


def test_local_exact_sha_selection_avoids_broken_search_filter():
    read=Mock(return_value=pages(ci(11,head_sha='b'*40),ci(10)))
    assert reader.select_ci_run('owner/repo',SHA,'artifact',read=read)['id']==10
    path=read.call_args.args[0][0]
    assert 'head_sha=' not in path and 'page=1' in path


@pytest.mark.parametrize('changed', [{'head_sha':'b'*40},{'head_branch':'main'},
    {'event':'pull_request'},{'status':'in_progress'},{'conclusion':'failure'}])
def test_artifact_selection_never_substitutes_other_sha_or_unverified_run(changed):
    with pytest.raises(ValueError,match='no successful exact-SHA'):
        reader.select_ci_run('owner/repo',SHA,'artifact',read=Mock(return_value=pages(ci(**changed))))


def test_empty_list_fails_explicitly_instead_of_building_empty_artifact_url():
    with pytest.raises(ValueError,match='no successful exact-SHA'):
        reader.select_ci_run('owner/repo',SHA,'artifact',read=Mock(return_value=pages()))


def test_pr_ci_does_not_reuse_older_success_over_newer_failure():
    read=Mock(return_value=pages(ci(10,event='pull_request'),ci(11,event='pull_request',conclusion='failure')))
    with pytest.raises(ValueError,match='latest exact-SHA PR CI'):
        reader.select_ci_run('owner/repo',SHA,'pr',read=read)


def test_pr_selection_preserves_emergency_fix_branch_support():
    run=ci(event='pull_request',head_branch='fix/example')
    assert reader.select_ci_run('owner/repo',SHA,'pr',read=Mock(return_value=pages(run)))==run


def test_bounded_pagination_reads_older_exact_sha():
    read=Mock(side_effect=[pages(*[ci(n,head_sha='b'*40) for n in range(100)]),pages(ci())])
    assert reader.select_ci_run('owner/repo',SHA,'artifact',read=read)['id']==10
    assert read.call_count==2 and 'page=2' in read.call_args.args[0][0]


def test_workflow_ci_lookup_never_uses_upstream_sha_search_index():
    for name in ['deploy-production-component.yml','deploy-production-linux.yml','align-test-after-main.yml']:
        text=Path('.github/workflows',name).read_text()
        assert 'actions/workflows/ci.yml/runs?head_sha=' not in text
        assert 'github_api_read.py select-ci-run' in text
    align=Path('.github/workflows/align-test-after-main.yml').read_text()
    assert '.check_suite_id == $suite' in align and 'length == 4 and all(.conclusion == "success")' in align


@pytest.mark.parametrize('run_id', [None, '', True, 0])
def test_missing_or_invalid_run_id_fails_before_artifact_url(run_id):
    with pytest.raises(ValueError,match='CI run ID'):
        reader.select_ci_run('owner/repo',SHA,'artifact',read=Mock(return_value=pages(ci(run_id))))
