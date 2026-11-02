from copy import deepcopy
import hashlib
import json

import pytest

from scripts.replay_public_minute_prefix import replay, read_bound
from qsys.data.quality.public_minute_prefix import EvidenceRejected


def build(tmp_path):
    def put(name, data, url=None):
        raw = data.encode() if isinstance(data, str) else json.dumps(data).encode()
        (tmp_path / name).write_bytes(raw)
        result = {'file': name, 'sha256': hashlib.sha256(raw).hexdigest()}
        if url is not None:result.update(url=url,http_status=200)
        return result
    wh = put('warehouse.json', {'partition_stable': True, 'partition_sha256': 'a'*64,
        'cases': [{'symbol':'603828.SH','first_positive':'2026-11-02 09:31:00'}],
        'rows':[{'symbol':'603828.SH','dt':'2026-11-02 09:30:00','volume':0,'amount':0,
                 'open':4.62,'high':4.62,'low':4.62,'close':4.62},
                {'symbol':'603828.SH','dt':'2026-11-02 09:31:00','volume':107,'amount':48476,
                 'open':4.53,'high':4.58,'low':4.53,'close':4.53}]})
    t = put('tencent.json', {'code':0,'data':{'sh603828':{'data':[{'date':'20261102',
        'data':['0930 4.62 0 0.00','0931 4.53 107 48476.00']}]}}},
        'https://web.ifzq.gtimg.cn/appstock/app/day/query?code=sh603828')
    s = put('sina.raw', 'var _data=([{"day":"2026-10-30 15:00:00","volume":"5","amount":"20"},'
        '{"day":"2026-11-02 09:31:00","volume":"10700","amount":"48476.0032"}]);',
        'https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/'
        'CN_MarketDataService.getKLineData?symbol=sh603828&scale=1&ma=no&datalen=300')
    m = {'protocol':'qsys583-public-prefix-input-v2','day':'2026-11-02',
         'warehouse':wh,'cases':[{'symbol':'603828.SH','tencent':t,'sina':s}]}
    path = tmp_path/'manifest.json';path.write_text(json.dumps(m))
    return path, m


def test_replay_does_not_create_production_pass_or_assert_full_market_equality(tmp_path):
    path,_ = build(tmp_path)
    result = replay(path)
    assert result['supported_count'] == result['case_count'] == 1
    assert result['production_quality_pass'] is False
    assert result['full_ohlcv_equality_verified'] is False
    assert result['upstream_feed_independence_verified'] is False
    assert result['warehouse_writes'] == result['network_calls'] == 0
    assert result['requires_review'] is True
    assert result['control_errors'] == ['control_roster_or_market_coverage_missing']


@pytest.mark.parametrize('mutation,reason', [
    ('sha','input_sha_changed'),('url','tencent_request_identity_mismatch'),
    ('http','public_response_not_successful'),('day','tencent_target_day_missing_or_duplicate'),
])
def test_untrusted_or_mismatched_source_keeps_failure(tmp_path, mutation, reason):
    path,m = build(tmp_path)
    if mutation=='sha': (tmp_path/'tencent.json').write_bytes(b'{}')
    if mutation=='url': m['cases'][0]['tencent']['url'] += '0'
    if mutation=='http': m['cases'][0]['sina']['http_status'] = 403
    if mutation=='day': m['day'] = '2026-11-03'
    path.write_text(json.dumps(m));result = replay(path)
    assert result['supported_count'] == 0 and reason in result['cases'][0]['errors']


def test_selected_warehouse_rows_cannot_override_bound_first_positive(tmp_path):
    path,m = build(tmp_path);p=tmp_path/'warehouse.json';d=json.loads(p.read_text())
    d['cases'][0]['first_positive']='2026-11-02 09:32:00';p.write_text(json.dumps(d))
    m['warehouse']['sha256']=hashlib.sha256(p.read_bytes()).hexdigest();path.write_text(json.dumps(m))
    result=replay(path)
    assert result['supported_count']==0
    assert result['cases'][0]['errors']==['warehouse_first_positive_binding_changed']


@pytest.mark.parametrize('roster',[[],['603828.SH','603828.SH']])
def test_no_empty_success_or_duplicate_case_voting(tmp_path,roster):
    path,m=build(tmp_path);case=m['cases'][0];m['cases']=[deepcopy(case) for _ in roster]
    path.write_text(json.dumps(m))
    with pytest.raises(EvidenceRejected,match='case_roster_changed'):replay(path)


def test_bound_file_cannot_escape_audit_directory(tmp_path):
    with pytest.raises(EvidenceRejected,match='input_outside_audit'):
        read_bound(tmp_path,{'file':'../outside.json','sha256':'a'*64})


def test_failed_case_does_not_discard_other_case_evidence(tmp_path):
    path,m=build(tmp_path)
    m['cases'][0]['sina']['sha256']='0'*64;path.write_text(json.dumps(m))
    result=replay(path)
    assert result['case_count']==1 and not result['cases'][0]['supported']
    assert result['cases'][0]['errors']==['input_sha_changed']


def test_positive_controls_cannot_be_silently_ignored_after_cases_match(tmp_path):
    path,m=build(tmp_path)
    controls=[]
    for symbol in ['600000.SH','000001.SZ','920285.BJ']:
        control=deepcopy(m['cases'][0]);control['symbol']=symbol
        # Deliberately mismatched request identity: never count malformed controls as agreement.
        controls.append(control)
    m['controls']=controls;path.write_text(json.dumps(m))
    result=replay(path)
    assert result['supported_count']==1
    assert result['first_positive_label_contract_consistent'] is False
    assert result['requires_review'] is True
    assert len(result['controls'])==3
    assert all(item['errors']==['tencent_request_identity_mismatch'] for item in result['controls'])


def test_valid_positive_controls_expose_auction_label_difference(tmp_path):
    path,m=build(tmp_path)
    controls=[]
    template=json.loads((tmp_path/'tencent.json').read_bytes())['data']['sh603828']
    for symbol in ['600000.SH','000001.SZ','920285.BJ']:
        code=symbol[-2:].lower()+symbol[:6]
        t=deepcopy(template);t['data'][0]['data'][0]='0930 4.62 1 4.62'
        if symbol.endswith('.BJ'):
            t['data'][0]['data']=[' '.join(row.split(' ')[:3]) for row in t['data'][0]['data']]
        tp=tmp_path/(symbol+'.tencent.json');tp.write_text(json.dumps({'code':0,'data':{code:t}}))
        controls.append({'symbol':symbol,
            'tencent':{'file':tp.name,'sha256':hashlib.sha256(tp.read_bytes()).hexdigest(),
                       'http_status':200,'url':'https://web.ifzq.gtimg.cn/appstock/app/day/query?code='+code},
            'sina':{**m['cases'][0]['sina'],'url':m['cases'][0]['sina']['url'].replace('sh603828',code)}})
    m['controls']=controls;path.write_text(json.dumps(m))
    result=replay(path)
    assert result['supported_count']==1
    assert result['requires_review'] is True
    assert not result['first_positive_label_contract_consistent']
    assert all(c['errors']==['control_first_positive_label_mismatch'] for c in result['controls'])
    assert result['production_quality_pass'] is False


def test_one_failed_case_retains_the_other_case_result(tmp_path):
    path,m=build(tmp_path)
    whp=tmp_path/'warehouse.json';wh=json.loads(whp.read_text())
    second=deepcopy(wh['cases'][0]);second['symbol']='001228.SZ';wh['cases'].append(second)
    wh['rows'] += [{**row,'symbol':'001228.SZ'} for row in wh['rows'][:]]
    whp.write_text(json.dumps(wh));m['warehouse']['sha256']=hashlib.sha256(whp.read_bytes()).hexdigest()
    cp=deepcopy(m['cases'][0]);cp['symbol']='001228.SZ'
    tp=tmp_path/'second-tencent.json';tp.write_bytes((tmp_path/'tencent.json').read_bytes().replace(b'sh603828',b'sz001228'))
    cp['tencent'].update(file=tp.name,sha256=hashlib.sha256(tp.read_bytes()).hexdigest(),
                         url=cp['tencent']['url'].replace('sh603828','sz001228'))
    cp['sina']['url']=cp['sina']['url'].replace('sh603828','sz001228')
    m['cases'].append(cp);m['cases'][0]['sina']['sha256']='0'*64
    path.write_text(json.dumps(m));result=replay(path)
    assert result['case_count']==2 and result['supported_count']==1
    assert not result['cases'][0]['supported'] and result['cases'][1]['supported']
