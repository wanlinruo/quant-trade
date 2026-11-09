"""Default-off QMT thin signal executor v2 (#601).

This file is installed into the QMT editor; it uses only the standard library
and must stay self-contained (no ``qsys`` imports).  The checked-in copy is
inert: the installer (``scripts/prepare_qmt_signal_executor.py``) replaces the
literals ``ENABLED``, ``RUN_ID``, ``CLIENT_BUILD``, ``ACCOUNT_ID``, ``KEY_HEX``
and, after the field probe, ``PRICE_TYPE_MARKET``.  Account values and the key
never enter Git.

The executor decides nothing about strategy.  It executes signed commands from
the Windows gateway, enforcing its own independent hard limits:

* buys only for Shanghai/Shenzhen stock/ETF code ranges; Beijing (#589) and
  STAR (68, no account permission) are refused; ChiNext (30) is allowed;
* buys are limit orders, lots of 100, notional <= ``MAX_ORDER_NOTIONAL`` and
  the day's cumulative buys <= ``MAX_DAILY_BUY_NOTIONAL``;
* a stale gateway heartbeat, the KILL word or a full report ring block new buys,
  while sells and cancels stay possible (legal reduction path);
* before ``passorder`` the current ORDER rows are searched for the remark, so a
  redelivered command never orders twice.

Every command gets a signed receipt: ``accepted`` (a native call was made or the
order already exists), ``rejected`` (no native call) or ``duplicate`` (the same
attempt was already handled).  ``accepted`` is not broker acceptance.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
import re
import uuid

PROTOCOL = 'qsys.qmt-signal.v1'
MAP_SIZE = 262144
REQUEST_OFFSET = 0
RESPONSE_OFFSET = 4096
CONTROL_OFFSET = 8192
EXECUTOR_OFFSET = 8448
COORDINATOR_MARKER_OFFSET = 8704
SCHEDULED_MARKER_OFFSET = 9216
RING_OFFSET = 16384
RING_SLOT_SIZE = 1024
RING_SLOTS = 240
FRAME_MAX = 3800
TRANSPORT_TTL_MS = 5000
KINDS = ('place', 'cancel', 'cancel_all', 'snapshot', 'ping')
RECEIPTS = ('accepted', 'rejected', 'duplicate')
REQUEST_FIELDS = frozenset({'protocol', 'run_id', 'seq', 'kind', 'attempt_id', 'session_nonce',
                            'command_sha256', 'command_json', 'issued_at_ms', 'expires_at_ms',
                            'mac'})

RUN_ID = ''
CLIENT_BUILD = ''
ACCOUNT_ID = ''
KEY_HEX = ''
ENABLED = False
PRICE_TYPE_MARKET = None
MAX_ORDER_NOTIONAL = 10000.0
MAX_DAILY_BUY_NOTIONAL = 10000.0
GATEWAY_STALE_MS = 10000
SCHEDULE_COUNT = 10 ** 7
STRATEGY_NAME = 'qsys601'

STOCK_BUY, STOCK_SELL = 23, 24
ORDER_TYPE_SHARES = 1101
PRICE_TYPE_LIMIT = 11
QUICK_TRADE_ALWAYS = 2
TERMINAL_STATUS = frozenset({53, 54, 56, 57})   # 52 部成待撤仍可能成交
CST = timezone(timedelta(hours=8))
CLIENT_ORDER_ID = re.compile(r'q[0-9]{9}[0-9a-f]{12}')
SYMBOL = re.compile(r'([0-9]{6})\.(SH|SZ|BJ)')
BUY_PREFIXES = {'SH': ('60', '51', '56', '58'), 'SZ': ('00', '30', '15', '16')}
PLACE_FIELDS = frozenset({'client_order_id', 'symbol', 'side', 'qty', 'order_type',
                          'limit_price', 'expires_at_ms'})

ORDER_FIELDS = ('m_strRemark', 'm_strOrderSysID', 'm_nOrderStatus', 'm_nOrderSubmitStatus',
                'm_nVolumeTotalOriginal', 'm_nVolumeTraded', 'm_nVolumeTotal',
                'm_dLimitPrice', 'm_dTradedPrice', 'm_dTradeAmount', 'm_dCancelAmount',
                'm_nOffsetFlag', 'm_nDirection', 'm_nOrderPriceType', 'm_nErrorID',
                'm_strErrorMsg', 'm_strCancelInfo', 'm_strInstrumentID', 'm_strExchangeID',
                'm_strInsertDate', 'm_strInsertTime')
DEAL_FIELDS = ('m_strRemark', 'm_strTradeID', 'm_strOrderSysID', 'm_dPrice', 'm_nVolume',
               'm_dTradeAmount', 'm_dCommission', 'm_dComssion', 'm_nOffsetFlag',
               'm_nDirection', 'm_strTradeDate', 'm_strTradeTime', 'm_strInstrumentID',
               'm_strExchangeID')
POSITION_FIELDS = ('m_strInstrumentID', 'm_strExchangeID', 'm_nVolume', 'm_nCanUseVolume',
                   'm_nFrozenVolume', 'm_nOnRoadVolume', 'm_nYesterdayVolume',
                   'm_dOpenPrice', 'm_dMarketValue')
ACCOUNT_FIELDS = ('m_dAvailable', 'm_dBalance', 'm_dStockValue', 'm_dFrozenCash',
                  'm_dInstrumentValue')
_STATE = None
_RAN = False


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def sign(value, key):
    return hmac.new(key, canonical(value), 'sha256').hexdigest()


def _unique(pairs):
    obj = {}
    for k, v in pairs:
        if k in obj:
            raise ValueError('duplicate_key')
        obj[k] = v
    return obj


def _no_constant(value):
    raise ValueError('nonfinite_json')


def decode(raw):
    return json.loads(raw, object_pairs_hook=_unique, parse_constant=_no_constant)


def valid_id(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{32}', value) is not None


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def coordinator_marker(run_id):
    return b'QSYS_QMT_SIGNAL:' + run_id.encode('ascii')


def scheduled_marker(run_id):
    return b'QSYS_QMT_SIGNAL_SCHEDULED:' + run_id.encode('ascii')


def plain(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:80]
    return None


def extract(obj, names):
    return {n: plain(getattr(obj, n)) for n in names if hasattr(obj, n)}


def _get(buf, offset, size):
    return int.from_bytes(bytes(buf[offset:offset + size]), 'little')


def _put(buf, offset, size, value):
    buf[offset:offset + size] = int(value).to_bytes(size, 'little')


def now_ms():
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class Executor:
    """Command executor; QMT globals are injected so tests run without QMT."""

    def __init__(self, run_id, key, api, buf, clock_ms=now_ms):
        if not valid_id(run_id) or type(key) is not bytes or len(key) != 32:
            raise ValueError('explicit executor binding required')
        self.run_id, self.key, self.api, self.buf, self.clock = run_id, key, api, buf, clock_ms
        self.epoch = uuid.uuid4().hex
        self.seen, self.daily, self.pending = {}, {}, []
        # Continue numbering from the shared latest word: only the gateway writes
        # the consumed cursor, so a restart can never race or rewind it.
        self.report_seq, self.kill_handled = _get(buf, EXECUTOR_OFFSET + 40, 8), False
        _put(buf, EXECUTOR_OFFSET + 56, 8, self.clock())
        buf[EXECUTOR_OFFSET:EXECUTOR_OFFSET + 32] = self.epoch.encode('ascii')
        self._beat()

    # -- shared words ---------------------------------------------------
    def _beat(self):
        _put(self.buf, EXECUTOR_OFFSET + 32, 8, self.clock())

    def beat_gateway(self, at_ms):
        """Test/diagnostic helper; production gateway writes this word itself."""
        _put(self.buf, CONTROL_OFFSET, 8, at_ms)

    def gateway_stale(self, now):
        return now - _get(self.buf, CONTROL_OFFSET, 8) > GATEWAY_STALE_MS

    def kill_engaged(self):
        return _get(self.buf, CONTROL_OFFSET + 8, 4) != 0

    def backlog(self):
        return len(self.pending)

    # -- report ring ----------------------------------------------------
    def _write(self, row):
        payload = canonical(row)
        if len(payload) > RING_SLOT_SIZE - 12:
            # Keep every numeric fact (status, quantities, prices) and the remark;
            # shorten free text first, then drop it.
            short = {k: (v[:8] if isinstance(v, str) and k != 'm_strRemark' else v)
                     for k, v in row.items()}
            short['oversize'] = True
            payload = canonical(short)
            if len(payload) > RING_SLOT_SIZE - 12:
                payload = canonical({k: v for k, v in short.items()
                                     if not isinstance(v, str) or k in ('m_strRemark', 'kind', 'epoch', 'source')})
        self.pending.append(payload)
        self.flush()

    def flush(self):
        consumed = _get(self.buf, CONTROL_OFFSET + 16, 8)
        while self.pending and self.report_seq - consumed < RING_SLOTS:
            payload = self.pending.pop(0)
            seq = self.report_seq + 1
            slot = RING_OFFSET + (seq % RING_SLOTS) * RING_SLOT_SIZE
            self.buf[slot + 12:slot + 12 + len(payload)] = payload
            _put(self.buf, slot + 4, 8, seq)
            _put(self.buf, slot, 4, len(payload))
            self.report_seq = seq
            _put(self.buf, EXECUTOR_OFFSET + 40, 8, seq)
        _put(self.buf, EXECUTOR_OFFSET + 48, 4, 1 if self.pending else 0)

    # -- broker facts ---------------------------------------------------
    def own(self, obj):
        remark = getattr(obj, 'm_strRemark', None)
        return isinstance(remark, str) and CLIENT_ORDER_ID.fullmatch(remark) is not None

    def account_match(self, obj):
        return getattr(obj, 'm_strAccountID', None) == ACCOUNT_ID

    def record_order(self, source, obj):
        if not self.own(obj):
            return False
        row = extract(obj, ORDER_FIELDS)
        row.update(kind='order', source=source, account_match=self.account_match(obj),
                   epoch=self.epoch)
        self._write(row)
        return True

    def record_deal(self, source, obj):
        if not self.own(obj):
            return False
        row = extract(obj, DEAL_FIELDS)
        row.update(kind='deal', source=source, account_match=self.account_match(obj),
                   epoch=self.epoch)
        self._write(row)
        return True

    def record_error(self, args, message):
        name = getattr(args, 'strategyName', '')
        remark = getattr(args, 'userOrderId', None)
        text = re.sub('[0-9]{6,20}', '#', message) if isinstance(message, str) else None
        self._write(dict(kind='order_error', epoch=self.epoch,
                         m_strRemark=remark if isinstance(remark, str)
                         and CLIENT_ORDER_ID.fullmatch(remark) else None,
                         strategy_match=name == STRATEGY_NAME,
                         message=plain(text),
                         orderCode=plain(getattr(args, 'orderCode', None))))

    def query(self, datatype):
        rows = self.api['get_trade_detail_data'](ACCOUNT_ID, 'STOCK', datatype)
        if rows is None:
            # An unavailable query must never look like "no orders" (#601 review).
            raise ValueError('query_unavailable:' + datatype)
        return list(rows)

    def orders_for(self, remark):
        return [o for o in self.query('ORDER') if getattr(o, 'm_strRemark', None) == remark]

    def snapshot(self):
        counts = dict(orders=0, deals=0, positions=0, accounts=0, active_orders=0, foreign_orders=0)
        for obj in self.query('ORDER'):
            own = self.record_order('query', obj)
            counts['orders'] += own
            counts['foreign_orders'] += not own
            counts['active_orders'] += getattr(obj, 'm_nOrderStatus', None) not in TERMINAL_STATUS
        for obj in self.query('DEAL'):
            counts['deals'] += self.record_deal('query', obj)
        for obj in self.query('POSITION'):
            row = extract(obj, POSITION_FIELDS)
            row.update(kind='position', source='query', account_match=self.account_match(obj),
                       epoch=self.epoch)
            self._write(row)
            counts['positions'] += 1
        for obj in self.query('ACCOUNT'):
            if self.account_match(obj):
                row = extract(obj, ACCOUNT_FIELDS)
                row.update(kind='account', source='query', epoch=self.epoch)
                self._write(row)
                counts['accounts'] += 1
        return counts

    def cancel_all(self, C):
        sent = 0
        for obj in self.query('ORDER'):
            if getattr(obj, 'm_nOrderStatus', None) not in TERMINAL_STATUS:
                self.api['cancel'](getattr(obj, 'm_strOrderSysID'), ACCOUNT_ID, 'STOCK', C)
                sent += 1
        return sent

    # -- commands -------------------------------------------------------
    def _day(self, now):
        return datetime.fromtimestamp(now / 1000, CST).date().isoformat()

    def check_place(self, cmd, now):
        if type(cmd) is not dict or set(cmd) != PLACE_FIELDS:
            return 'place_fields'
        cid, symbol, side, qty = cmd['client_order_id'], cmd['symbol'], cmd['side'], cmd['qty']
        if type(cid) is not str or CLIENT_ORDER_ID.fullmatch(cid) is None:
            return 'client_order_id'
        if type(cmd['expires_at_ms']) is not int or now >= cmd['expires_at_ms']:
            return 'command_expired'
        match = SYMBOL.fullmatch(symbol) if type(symbol) is str else None
        if match is None or side not in ('buy', 'sell') or type(qty) is not int or qty <= 0:
            return 'place_shape'
        price = cmd['limit_price']
        if cmd['order_type'] == 'limit':
            if (type(price) is not float or not math.isfinite(price) or price <= 0
                    or round(price, 3) != price):
                return 'limit_price'
        elif cmd['order_type'] == 'market':
            if side == 'buy':
                return 'buy_requires_limit'
            if price is not None:
                return 'limit_price'
            if PRICE_TYPE_MARKET is None:
                return 'market_price_type_unverified'
        else:
            return 'order_type'
        if side == 'sell':
            return None
        if cmd['order_type'] != 'limit':
            return 'buy_requires_limit'
        code, exchange = match.groups()
        if exchange not in BUY_PREFIXES or not code.startswith(BUY_PREFIXES[exchange]):
            return 'market_blocked'
        if qty % 100:
            return 'qty_lot'
        if self.kill_engaged():
            return 'kill_engaged'
        if self.gateway_stale(now):
            return 'gateway_stale'
        if self.pending:
            return 'ring_full'
        notional = price * qty
        if notional > MAX_ORDER_NOTIONAL:
            return 'order_notional'
        if self.spent_today(now) + notional > MAX_DAILY_BUY_NOTIONAL:
            return 'daily_buy_limit'
        return None

    def spent_today(self, now):
        """Max of in-memory spend and today's own buy orders at the counter, so a
        QMT rebuild never resets the cap (#601 review)."""
        day = self._day(now)
        counter = 0.0
        stamp = day.replace('-', '')
        for obj in self.query('ORDER'):
            if (self.own(obj) and getattr(obj, 'm_nOffsetFlag', None) == 48
                    and getattr(obj, 'm_strInsertDate', None) == stamp):
                price = getattr(obj, 'm_dLimitPrice', None)
                qty = getattr(obj, 'm_nVolumeTotalOriginal', None)
                if not isinstance(price, (int, float)) or not isinstance(qty, int):
                    raise ValueError('order_amount_unknown')
                counter += float(price) * qty
        return max(self.daily.get(day, 0.0), counter)

    def place(self, C, cmd, now):
        cid = cmd['client_order_id']
        existing = self.orders_for(cid)
        if existing:
            for obj in existing:
                self.record_order('query', obj)
            return dict(status='already_present', orders=len(existing))
        if cmd['side'] == 'buy':
            day = self._day(now)
            # Spend the limit before the native call: an exception never refunds it.
            self.daily[day] = self.daily.get(day, 0.0) + cmd['limit_price'] * cmd['qty']
        op = STOCK_BUY if cmd['side'] == 'buy' else STOCK_SELL
        if cmd['order_type'] == 'limit':
            pr_type, price = PRICE_TYPE_LIMIT, cmd['limit_price']
        else:
            pr_type, price = PRICE_TYPE_MARKET, 0.0
        try:
            self.api['passorder'](op, ORDER_TYPE_SHARES, ACCOUNT_ID, cmd['symbol'], pr_type,
                                  price, cmd['qty'], STRATEGY_NAME, QUICK_TRADE_ALWAYS, cid, C)
        except Exception as exc:
            return dict(status='call_exception', error_type=type(exc).__name__)
        return dict(status='passorder_called')

    def cancel(self, C, cmd):
        if type(cmd) is not dict or set(cmd) != {'client_order_id'}:
            return None, 'cancel_fields'
        orders = self.orders_for(cmd['client_order_id'])
        for obj in orders:
            self.record_order('query', obj)
        if not orders:
            return None, 'cancel_target_absent'
        live = [o for o in orders if getattr(o, 'm_nOrderStatus', None) not in TERMINAL_STATUS]
        if not live:
            return dict(status='cancel_not_needed'), None
        try:
            sent = [bool(self.api['cancel'](getattr(o, 'm_strOrderSysID'), ACCOUNT_ID, 'STOCK', C))
                    for o in live]
        except Exception as exc:
            return dict(status='call_exception', error_type=type(exc).__name__), None
        return dict(status='cancel_signal_sent', sent=sent), None

    def reply(self, obj, status, reason, result=None):
        row = dict(protocol=PROTOCOL, run_id=self.run_id, epoch=self.epoch,
                   attempt_id=obj.get('attempt_id') if valid_id(obj.get('attempt_id')) else None,
                   session_nonce=obj.get('session_nonce') if valid_id(obj.get('session_nonce')) else None,
                   command_sha256=obj.get('command_sha256') if valid_sha(obj.get('command_sha256')) else None,
                   seq=obj.get('seq') if type(obj.get('seq')) is int else None,
                   status=status, reason=reason, result=result)
        row['mac'] = sign(row, self.key)
        return canonical(row)

    def receive(self, C, raw):
        now = self.clock()
        if type(raw) is not bytes or not 1 <= len(raw) <= FRAME_MAX:
            raise ValueError('frame_size')
        obj = decode(raw.decode('ascii'))
        if type(obj) is not dict:
            raise ValueError('frame_object')
        if set(obj) != REQUEST_FIELDS:
            return self.reply(obj, 'rejected', 'fields')
        body = dict(obj)
        mac = body.pop('mac')
        if not valid_sha(mac) or not hmac.compare_digest(mac, sign(body, self.key)):
            return self.reply(obj, 'rejected', 'signature')
        if obj['protocol'] != PROTOCOL or obj['run_id'] != self.run_id or obj['kind'] not in KINDS:
            return self.reply(obj, 'rejected', 'binding')
        if (not valid_id(obj['attempt_id']) or not valid_id(obj['session_nonce'])
                or not valid_sha(obj['command_sha256']) or type(obj['seq']) is not int
                or type(obj['command_json']) is not str):
            return self.reply(obj, 'rejected', 'identity')
        if hashlib.sha256(obj['command_json'].encode('ascii')).hexdigest() != obj['command_sha256']:
            return self.reply(obj, 'rejected', 'command_sha')
        start, end = obj['issued_at_ms'], obj['expires_at_ms']
        if (type(start) is not int or type(end) is not int or not 0 < end - start <= TRANSPORT_TTL_MS
                or not start <= now < end):
            return self.reply(obj, 'rejected', 'transport_expired')
        normalized = canonical(obj)
        # Attempts older than twice the transport TTL can no longer be replayed
        # (transport_expired), so forget them; QMT runs for days.
        horizon = now - 2 * TRANSPORT_TTL_MS
        for attempt in [a for a, v in self.seen.items() if v[2] < horizon]:
            del self.seen[attempt]
        old = self.seen.get(obj['attempt_id'])
        if old is not None:
            if old[0] != normalized:
                return self.reply(obj, 'rejected', 'attempt_conflict')
            first = json.loads(old[1])
            return self.reply(obj, 'duplicate', first['reason'], first['result'])
        cmd = decode(obj['command_json'])
        kind = obj['kind']
        if kind == 'place':
            reason = self.check_place(cmd, now)
            response = (self.reply(obj, 'rejected', reason) if reason
                        else self.reply(obj, 'accepted', 'ok', self.place(C, cmd, now)))
        elif kind == 'cancel':
            result, reason = self.cancel(C, cmd)
            response = (self.reply(obj, 'rejected', reason) if reason
                        else self.reply(obj, 'accepted', 'ok', result))
        elif kind == 'cancel_all':
            response = self.reply(obj, 'accepted', 'ok', dict(sent=self.cancel_all(C)))
        elif kind == 'snapshot':
            try:
                result = self.snapshot()
            except Exception as exc:
                result = dict(status='call_exception', error_type=type(exc).__name__)
            response = self.reply(obj, 'accepted', 'ok', result)
        else:
            response = self.reply(obj, 'accepted', 'ok', dict(pong=True))
        self.seen[obj['attempt_id']] = (normalized, response, now)
        return response

    # -- scheduler tick -------------------------------------------------
    def tick(self, C):
        self._beat()
        self.flush()
        if self.kill_engaged():
            if not self.kill_handled:
                self.cancel_all(C)            # raises → retried on the next tick
                self.kill_handled = True
        else:
            self.kill_handled = False
        generation = _get(self.buf, REQUEST_OFFSET, 4)
        if generation == 0 or generation == _get(self.buf, RESPONSE_OFFSET, 4):
            return
        size = _get(self.buf, REQUEST_OFFSET + 4, 4)
        if not 1 <= size <= FRAME_MAX:
            raise ValueError('request frame size')
        raw = bytes(self.buf[REQUEST_OFFSET + 8:REQUEST_OFFSET + 8 + size])
        if _get(self.buf, REQUEST_OFFSET, 4) != generation:
            return
        reply = self.receive(C, raw)
        _put(self.buf, RESPONSE_OFFSET + 4, 4, len(reply))
        self.buf[RESPONSE_OFFSET + 8:RESPONSE_OFFSET + 8 + len(reply)] = reply
        _put(self.buf, RESPONSE_OFFSET, 4, generation)


def emit(status, **facts):
    row = dict(protocol=PROTOCOL, run_id=RUN_ID if valid_id(RUN_ID) else None, status=status)
    row.update(facts)
    print(repr(row))


def on_timer(C):
    state = _STATE
    if state is None or state['stopped']:
        return
    try:
        state['executor'].tick(C)
    except Exception as exc:
        emit('tick_failed', error_type=type(exc).__name__)


def _active():
    state = _STATE
    return state['executor'] if state is not None and not state['stopped'] else None


def order_callback(C, orderInfo):
    ex = _active()
    if ex is not None:
        try:
            ex.record_order('callback', orderInfo)
        except Exception as exc:
            emit('order_callback_failed', error_type=type(exc).__name__)


def deal_callback(C, dealInfo):
    ex = _active()
    if ex is not None:
        try:
            ex.record_deal('callback', dealInfo)
        except Exception as exc:
            emit('deal_callback_failed', error_type=type(exc).__name__)


def orderError_callback(C, orderArgs, errMsg):
    ex = _active()
    if ex is not None:
        try:
            ex.record_error(orderArgs, errMsg)
        except Exception as exc:
            emit('order_error_callback_failed', error_type=type(exc).__name__)


def init(C):
    global _STATE, _RAN
    if _RAN:
        return
    _RAN = True
    memory, stage = None, 'configuration'
    try:
        if ENABLED is not True:
            emit('disabled')
            return
        if (not valid_id(RUN_ID) or CLIENT_BUILD != '2.1.33.0'
                or re.fullmatch('[0-9]{6,20}', ACCOUNT_ID) is None
                or re.fullmatch('[0-9a-f]{64}', KEY_HEX) is None
                or PRICE_TYPE_MARKET not in (None, 5)):
            raise ValueError('execution binding')
        api = {n: globals().get(n) for n in ('passorder', 'cancel', 'get_trade_detail_data')}
        if not all(callable(fn) for fn in api.values()):
            raise ValueError('native trading api unavailable')
        for name in ('schedule_run', 'cancel_schedule_run', 'set_account'):
            if not callable(getattr(C, name, None)):
                raise ValueError('scheduler and account binding required')
        stage = 'open_named_memory'
        import mmap
        memory = mmap.mmap(-1, MAP_SIZE, tagname='Local\\qsys_qmt_signal_' + RUN_ID,
                           access=mmap.ACCESS_WRITE)
        marker = coordinator_marker(RUN_ID)
        if bytes(memory[COORDINATOR_MARKER_OFFSET:COORDINATOR_MARKER_OFFSET + len(marker)]) != marker:
            raise ValueError('gateway coordinator marker required')
        executor = Executor(RUN_ID, bytes.fromhex(KEY_HEX), api, memory)
        stage = 'set_account'
        C.set_account(ACCOUNT_ID)
        _STATE = dict(memory=memory, executor=executor, stopped=False)
        stage = 'schedule_run'
        task_id = C.schedule_run(on_timer, datetime.now() + timedelta(seconds=2),
                                 SCHEDULE_COUNT, timedelta(seconds=1), 'qsys601s_' + RUN_ID)
        stage = 'publish_scheduled_marker'
        done = scheduled_marker(RUN_ID)
        memory[SCHEDULED_MARKER_OFFSET:SCHEDULED_MARKER_OFFSET + len(done)] = done
        emit('scheduled', task_id=task_id, epoch=executor.epoch)
    except Exception as exc:
        emit('init_failed', stage=stage, error_type=type(exc).__name__)
        if _STATE is None and memory is not None:
            memory.close()


def stop(C):
    state = _STATE
    if state is None or state['stopped']:
        return
    state['stopped'] = True
    try:
        C.cancel_schedule_run('qsys601s_' + RUN_ID)
    except Exception as exc:
        emit('cancel_schedule_failed', error_type=type(exc).__name__)
    emit('stopped')


def handlebar(C):
    pass
