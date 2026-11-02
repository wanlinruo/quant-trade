"""#583 one-shot access check; output excludes configuration and error messages."""
import datetime
import json
from pathlib import Path
import tomllib
import urllib.request


def main():
    report = {
        'at': datetime.datetime.now().astimezone().isoformat(),
        'api': 'stk_auction_o',
        'params': {'ts_code': '603828.SH', 'trade_date': '20261102'},
        'fields': 'ts_code,trade_date,open,close,high,low,vol,amount,vwap',
        'status': 'failed', 'market_writes': 0, 'api_attempts': 0,
    }
    try:
        cfg = tomllib.loads(Path('/etc/qsys/components/tushare-sync/deploy.toml').read_text())
        token = cfg['tushare']['token']
        body = json.dumps({'api_name': report['api'], 'params': report['params'],
                           'fields': report['fields'], 'token': token}).encode()
        report['api_attempts'] = 1
        request = urllib.request.Request('https://api.tushare.pro', data=body,
                                        headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
        report['code'] = data.get('code')
        if data.get('code') == 0:
            fields = data.get('data', {}).get('fields', [])
            report['rows'] = [dict(zip(fields, row))
                              for row in data.get('data', {}).get('items', [])]
            report['status'] = 'completed'
        else:
            report['status'] = ('permission_denied' if '权限' in str(data.get('msg', ''))
                                else 'request_rejected')
    except Exception as exc:
        report['error_type'] = type(exc).__name__
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
