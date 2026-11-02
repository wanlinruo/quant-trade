"""#583 offline replay; no network, warehouse writes, or production health mutation."""
from __future__ import annotations

import argparse
from datetime import date, datetime
import hashlib
import json
import re
from pathlib import Path

from qsys.data.quality.public_minute_prefix import (
    EvidenceRejected, assess_prefix, code_for, require, sina_points, tencent_points,
)


def read_bound(root: Path, entry: dict) -> bytes:
    path = (root / entry['file']).resolve()
    require(path.is_relative_to(root), 'input_outside_audit')
    raw = path.read_bytes()
    require(len(raw) <= 2_000_000, 'input_size_limit')
    require(hashlib.sha256(raw).hexdigest() == entry['sha256'], 'input_sha_changed')
    return raw


def public_points(root: Path, case: dict, day: date):
    require(case["tencent"]["http_status"] == 200 and case["sina"]["http_status"] == 200,
            "public_response_not_successful")
    code = code_for(case["symbol"])
    require(case["tencent"]["url"] ==
            "https://web.ifzq.gtimg.cn/appstock/app/day/query?code=" + code,
            "tencent_request_identity_mismatch")
    require(case["sina"]["url"] ==
            "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/"
            "CN_MarketDataService.getKLineData?symbol=" + code + "&scale=1&ma=no&datalen=300",
            "sina_request_identity_mismatch")
    return (tencent_points(read_bound(root, case["tencent"]), case["symbol"], day),
            sina_points(read_bound(root, case["sina"]), day))


def replay(path: Path) -> dict:
    root = path.resolve().parent
    manifest_raw = path.read_bytes()
    manifest = json.loads(manifest_raw)
    require(manifest['protocol'] == 'qsys583-public-prefix-input-v2', 'manifest_protocol')
    day = date.fromisoformat(manifest['day'])
    warehouse = json.loads(read_bound(root, manifest['warehouse']))
    require(warehouse['partition_stable'] is True, 'warehouse_collection_drift')
    require(isinstance(warehouse['partition_sha256'], str) and bool(
        re.fullmatch('[0-9a-f]{64}', warehouse['partition_sha256'])), 'partition_sha_invalid')
    cases = warehouse['cases']
    expected = sorted(case['symbol'] for case in cases)
    supplied = sorted(case['symbol'] for case in manifest['cases'])
    require(expected == sorted(set(expected)) and supplied == expected and bool(expected),
            'case_roster_changed')
    results = []
    for case in manifest['cases']:
        symbol = case['symbol']
        item = {'symbol': symbol, 'supported': False, 'errors': []}
        try:
            tencent, sina = public_points(root, case, day)
            rows = [row for row in warehouse['rows'] if row['symbol'] == symbol]
            item = assess_prefix(symbol, day, rows, tencent, sina)
            expected = next(row for row in cases if row["symbol"] == symbol)["first_positive"]
            require(item.get("warehouse_first_positive") ==
                    datetime.fromisoformat(expected).isoformat(),
                    "warehouse_first_positive_binding_changed")
        except (EvidenceRejected, ValueError, TypeError, KeyError, OSError) as exc:
            item['supported'] = False
            item['errors'] = [str(exc) if isinstance(exc, EvidenceRejected) else type(exc).__name__]
        results.append(item)
    controls = []
    control_errors = []
    supplied_controls = manifest.get("controls", [])
    symbols = [entry["symbol"] for entry in supplied_controls]
    if (not supplied_controls or len(symbols) != len(set(symbols))
            or set(symbols) & set(supplied)
            or {symbol.split(".")[-1] for symbol in symbols} != {"SH", "SZ", "BJ"}):
        control_errors.append("control_roster_or_market_coverage_missing")
    for case in supplied_controls:
        item = {"symbol": case["symbol"], "labels_agree": False, "errors": []}
        try:
            tencent, sina = public_points(root, case, day)
            first_tencent = next((point.at for point in tencent if point.volume > 0), None)
            first_sina = next((point.at for point in sina if point.volume > 0), None)
            require(first_tencent is not None and first_sina is not None,
                    "control_positive_observation_missing")
            require(first_tencent == datetime(day.year, day.month, day.day, 9, 30),
                    "control_auction_positive_missing")
            item.update(tencent_first_positive=first_tencent.isoformat(),
                        sina_first_positive=first_sina.isoformat(),
                        labels_agree=first_tencent == first_sina)
            if not item["labels_agree"]:
                item["errors"].append("control_first_positive_label_mismatch")
        except (EvidenceRejected, ValueError, TypeError, KeyError, OSError) as exc:
            item["errors"] = [str(exc) if isinstance(exc, EvidenceRejected) else type(exc).__name__]
        controls.append(item)
    label_contract = bool(controls) and not control_errors and all(c["labels_agree"] for c in controls)
    require(path.read_bytes() == manifest_raw, "manifest_changed_during_replay")
    return {
        'protocol': 'qsys583-public-prefix-replay-v2', 'day': day.isoformat(),
        'input_manifest_sha256': hashlib.sha256(manifest_raw).hexdigest(),
        'bound_partition_sha256': warehouse['partition_sha256'],
        'case_count': len(results), 'supported_count': sum(item['supported'] for item in results),
        'cases': results, 'first_positive_timing_only': True,
        'full_ohlcv_equality_verified': False, 'upstream_feed_independence_verified': False,
        'controls': controls, 'control_errors': control_errors,
        'first_positive_label_contract_consistent': label_contract,
        'requires_review': not label_contract or not all(case['supported'] for case in results),
        'production_quality_pass': False, 'warehouse_writes': 0, 'network_calls': 0,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args(argv)
    try:
        result = replay(args.manifest)
    except Exception as exc:
        print(json.dumps({'replay_completed': False, 'error_type': type(exc).__name__}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result['requires_review'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
