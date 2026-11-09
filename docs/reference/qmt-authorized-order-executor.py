"""Default-off QMT thin executor for one pre-authorized order plan; #601.

The checked-in source is inert.  A reviewed installation for a single
maintainer-authorized window must replace the literals ``ENABLED``,
``RUN_ID``, ``CLIENT_BUILD``, ``ACCOUNT_ID``, ``KEY_HEX`` and
``AUTHORIZED_PLAN``.  Account values and the key never enter Git.

Safety model:

* The executor never decides anything.  It only executes plan steps whose
  canonical JSON is byte-identical to the installed ``AUTHORIZED_PLAN``; every
  step runs at most once per run and step numbers only move forward.
* A ``place`` step first queries current ORDER rows for its remark
  (``userOrderId``); an existing row is reported instead of ordering again, so
  a restarted model can never duplicate an order.
* Only Shanghai/Shenzhen main-board stock and ETF codes are accepted; Beijing
  codes are refused (#589).  Quantity is a positive multiple of 100 not above
  ``MAX_QTY`` and every order's notional is capped by ``MAX_NOTIONAL``.
* Broker facts (order/deal/error callbacks and current queries) are recorded
  only for objects whose remark belongs to this plan, using whitelisted fields
  and never the account id.  ``passorder`` returning is not broker acceptance.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
import re

PROTOCOL = 'qsys.authorized-order.v1'
SCOPE = 'execution:authorized-order'
FIELDS = {'protocol', 'scope', 'kind', 'run_id', 'seq', 'attempt_id', 'session_nonce',
          'command_sha256', 'command_json', 'issued_at_ms', 'expires_at_ms', 'mac'}
RUN_ID = ''
CLIENT_BUILD = ''
ACCOUNT_ID = ''
KEY_HEX = ''
AUTHORIZED_PLAN = ''
ENABLED = False
INSTANCE_MODE = 'authorized-order'
STRATEGY_NAME = 'qsys601'
MAX_QTY = 100
MAX_NOTIONAL = 1000.0
MAX_TICKS = 900
SNAPSHOT_LIMIT = 20

MAP_SIZE = 65536
REQUEST_OFFSET = 0
RESPONSE_OFFSET = 4096
COORDINATOR_MARKER_OFFSET = 6144
SCHEDULED_MARKER_OFFSET = 7168
REPORT_HEADER_OFFSET = 8192
REPORT_SLOTS_OFFSET = 8200
REPORT_SLOT_SIZE = 1024
REPORT_LIMIT = 50

STOCK_BUY, STOCK_SELL = 23, 24
ORDER_TYPE_SHARES = 1101
PRICE_TYPE_LIMIT = 11
QUICK_TRADE_ALWAYS = 2
TERMINAL_STATUS = {54, 56, 57}
SYMBOL = re.compile(r'(60[0-9]{4}|51[0-9]{4}|00[0-9]{4}|15[0-9]{4})\.(SH|SZ)')
ORDER_ID = re.compile(r'q601-[0-9a-f]{8}-[a-z]|q[0-9]{9}[0-9a-f]{12}')
PRICE_TYPE_LATEST = 5   # original XtQuantAdapter 'market' = LATEST_PRICE; sells only

ORDER_FIELDS = ('m_strRemark', 'm_strOrderSysID', 'm_nOrderStatus', 'm_nOrderSubmitStatus',
                'm_nVolumeTotalOriginal', 'm_nVolumeTraded', 'm_nVolumeTotal',
                'm_dLimitPrice', 'm_dTradedPrice', 'm_dTradeAmount', 'm_dCancelAmount',
                'm_nOffsetFlag', 'm_nDirection', 'm_nOrderPriceType', 'm_nErrorID',
                'm_strErrorMsg', 'm_strCancelInfo', 'm_strInstrumentID', 'm_strExchangeID',
                'm_strInsertDate', 'm_strInsertTime')
DEAL_FIELDS = ('m_strRemark', 'm_strTradeID', 'm_strOrderSysID', 'm_dPrice', 'm_nVolume',
               'm_dTradeAmount', 'm_dCommission', 'm_dComission', 'm_dComssion',
               'm_nOffsetFlag', 'm_nDirection', 'm_strTradeDate', 'm_strTradeTime',
               'm_strInstrumentID', 'm_strExchangeID')
POSITION_FIELDS = ('m_strInstrumentID', 'm_strExchangeID', 'm_nVolume', 'm_nCanUseVolume',
                   'm_nFrozenVolume', 'm_nOnRoadVolume', 'm_nYesterdayVolume')
_STATE = None
_RAN = False


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError('duplicate_key')
        obj[key] = value
    return obj


def reject_constant(value):
    raise ValueError('nonfinite_json')


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)


def valid_id(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{32}', value) is not None


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def coordinator_marker(run_id):
    return b'QSYS_AUTHORIZED_ORDER_ONLY:' + run_id.encode('ascii')


def scheduled_marker(run_id):
    if not valid_id(run_id):
        raise ValueError('explicit scheduled marker binding required')
    return b'QSYS_ORDER_SCHEDULED:' + run_id.encode('ascii')


def now_ms():
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def parse_utc_ms(text):
    if type(text) is not str or not 1 <= len(text) <= 64:
        raise ValueError('plan_time')
    value = datetime.fromisoformat(text)
    if value.utcoffset() != timedelta(0):
        raise ValueError('plan_time_utc')
    return int(value.timestamp() * 1000)


def validate_plan(raw, run_id):
    """Return the parsed plan or raise; nothing is executed on failure."""
    if type(raw) is not str or not 1 <= len(raw) <= 4000:
        raise ValueError('plan_size')
    plan = decode(raw)
    if type(plan) is not dict or set(plan) != {'run_id', 'not_before', 'not_after', 'steps'}:
        raise ValueError('plan_fields')
    if plan['run_id'] != run_id:
        raise ValueError('plan_run')
    start, end = parse_utc_ms(plan['not_before']), parse_utc_ms(plan['not_after'])
    if not 0 < end - start <= 3 * 3600 * 1000:
        raise ValueError('plan_window')
    steps = plan['steps']
    if type(steps) is not list or not 1 <= len(steps) <= 8:
        raise ValueError('plan_steps')
    placed = {}
    for index, step in enumerate(steps, 1):
        if type(step) is not dict or step.get('step') != index or type(step.get('step')) is not int:
            raise ValueError('plan_step_index')
        if step.get('action') == 'place':
            base = {'step', 'action', 'client_order_id', 'symbol', 'side', 'qty', 'limit_price'}
            latest = set(step) == base | {'price_type'}
            if set(step) != base and not latest:
                raise ValueError('plan_place_fields')
            cid = step['client_order_id']
            bound = (cid[5:13] == run_id[:8] if type(cid) is str and cid.startswith('q601-')
                     else type(cid) is str and cid[-12:] == run_id[:12])
            if (type(cid) is not str or ORDER_ID.fullmatch(cid) is None or not bound
                    or cid in placed):
                raise ValueError('plan_order_identity')
            if type(step['symbol']) is not str or SYMBOL.fullmatch(step['symbol']) is None:
                raise ValueError('plan_symbol')
            if (step['symbol'].startswith(('60', '51')) != step['symbol'].endswith('.SH')):
                raise ValueError('plan_symbol_exchange')
            if step['side'] not in ('buy', 'sell'):
                raise ValueError('plan_side')
            qty = step['qty']
            if type(qty) is not int or qty <= 0 or qty % 100 or qty > MAX_QTY:
                raise ValueError('plan_qty')
            price = step['limit_price']
            if latest:
                # Latest-price orders are only for selling an existing holding.
                if (step['side'] != 'sell' or price is not None
                        or type(step['price_type']) is not int
                        or step['price_type'] != PRICE_TYPE_LATEST):
                    raise ValueError('plan_price')
            elif (type(price) is not float or not math.isfinite(price) or price <= 0
                    or round(price, 3) != price or price * qty > MAX_NOTIONAL):
                raise ValueError('plan_price')
            placed[cid] = index
        elif step.get('action') == 'cancel':
            if set(step) != {'step', 'action', 'client_order_id'}:
                raise ValueError('plan_cancel_fields')
            if step['client_order_id'] not in placed:
                raise ValueError('plan_cancel_target')
        else:
            raise ValueError('plan_action')
    plan['window_ms'] = (start, end)
    plan['remarks'] = frozenset(placed)
    return plan


def sign(value, key):
    return hmac.new(key, canonical(value), 'sha256').hexdigest()


def plain(value):
    """Keep only JSON-safe scalars; non-finite floats become None."""
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
    row = {}
    for name in names:
        if hasattr(obj, name):
            row[name] = plain(getattr(obj, name))
    return row


class Executor:
    """Plan executor; QMT globals are injected so tests can run without QMT."""

    def __init__(self, run_id, key, plan, api, write_report):
        if not valid_id(run_id) or type(key) is not bytes or len(key) != 32:
            raise ValueError('explicit executor binding required')
        self.run_id, self.key, self.plan, self.api = run_id, key, plan, api
        self.write_report = write_report
        self.next_step, self.snapshots, self.seen = 1, 0, {}

    # -- broker facts -------------------------------------------------
    def account_matches(self, obj):
        return getattr(obj, 'm_strAccountID', None) == ACCOUNT_ID

    def record_order(self, source, obj):
        row = extract(obj, ORDER_FIELDS)
        if row.get('m_strRemark') not in self.plan['remarks']:
            return False
        row.update(kind='order', source=source, account_match=self.account_matches(obj))
        self.write_report(row)
        return True

    def record_deal(self, source, obj):
        row = extract(obj, DEAL_FIELDS)
        if row.get('m_strRemark') not in self.plan['remarks']:
            return False
        row.update(kind='deal', source=source, account_match=self.account_matches(obj))
        self.write_report(row)
        return True

    def record_error(self, args, message):
        name = getattr(args, 'strategyName', '')
        remark = next((r for r in self.plan['remarks'] if isinstance(name, str) and r in name), None)
        # Unmatched errors are still evidence (the remark may be carried elsewhere);
        # they never gate the driver because they carry no plan remark.
        self.write_report(dict(kind='order_error' if remark else 'order_error_unmatched',
                               m_strRemark=remark,
                               message=plain(message if isinstance(message, str) else None),
                               orderCode=plain(getattr(args, 'orderCode', None)),
                               prType=plain(getattr(args, 'prType', None))))
        return remark is not None

    def query(self, datatype):
        rows = self.api['get_trade_detail_data'](ACCOUNT_ID, 'STOCK', datatype)
        return list(rows) if rows is not None else []

    def orders_for(self, remark):
        return [o for o in self.query('ORDER') if getattr(o, 'm_strRemark', None) == remark]

    def snapshot(self):
        orders = deals = positions = 0
        for obj in self.query('ORDER'):
            orders += self.record_order('query', obj)
        for obj in self.query('DEAL'):
            deals += self.record_deal('query', obj)
        codes = {s['symbol'].split('.')[0] for s in self.plan['steps'] if s['action'] == 'place'}
        for obj in self.query('POSITION'):
            if getattr(obj, 'm_strInstrumentID', None) in codes:
                row = extract(obj, POSITION_FIELDS)
                row.update(kind='position', source='query', account_match=self.account_matches(obj))
                self.write_report(row)
                positions += 1
        return dict(orders=orders, deals=deals, positions=positions)

    # -- plan steps ---------------------------------------------------
    def run_step(self, C, step):
        cid = step['client_order_id']
        if step['action'] == 'place':
            existing = self.orders_for(cid)
            if existing:
                for obj in existing:
                    self.record_order('query', obj)
                return dict(status='already_present', orders=len(existing))
            op = STOCK_BUY if step['side'] == 'buy' else STOCK_SELL
            pr_type, price = ((PRICE_TYPE_LATEST, 0.0) if 'price_type' in step
                              else (PRICE_TYPE_LIMIT, step['limit_price']))
            self.api['passorder'](op, ORDER_TYPE_SHARES, ACCOUNT_ID, step['symbol'],
                                  pr_type, price, step['qty'],
                                  STRATEGY_NAME, QUICK_TRADE_ALWAYS, cid, C)
            return dict(status='passorder_called')
        orders = self.orders_for(cid)
        for obj in orders:
            self.record_order('query', obj)
        if not orders:
            return dict(status='cancel_target_absent')
        if len(orders) != 1:
            return dict(status='cancel_target_ambiguous', orders=len(orders))
        status = getattr(orders[0], 'm_nOrderStatus', None)
        if status in TERMINAL_STATUS:
            return dict(status='cancel_not_needed', order_status=plain(status))
        sent = self.api['cancel'](getattr(orders[0], 'm_strOrderSysID'), ACCOUNT_ID, 'STOCK', C)
        return dict(status='cancel_signal_sent', cancel_result=bool(sent))

    def reply(self, obj, reason, result=None):
        row = dict(protocol=PROTOCOL, run_id=self.run_id,
                   attempt_id=obj.get('attempt_id') if valid_id(obj.get('attempt_id')) else None,
                   session_nonce=obj.get('session_nonce') if valid_id(obj.get('session_nonce')) else None,
                   command_sha256=obj.get('command_sha256') if valid_sha(obj.get('command_sha256')) else None,
                   seq=obj.get('seq') if type(obj.get('seq')) is int else None,
                   status='handled' if reason == 'ok' else 'rejected', reason=reason,
                   result=result, broker_accepted=None)
        row['mac'] = sign(row, self.key)
        return canonical(row)

    def receive(self, C, raw, now):
        if type(raw) is not bytes or not 1 <= len(raw) <= 4000 or type(now) is not int:
            raise ValueError('execution_frame')
        obj = decode(raw.decode('ascii'))
        if type(obj) is not dict:
            raise ValueError('execution_object')
        if set(obj) != FIELDS:
            return self.reply(obj, 'fields')
        mac = obj.pop('mac')
        if not valid_sha(mac) or not hmac.compare_digest(mac, sign(obj, self.key)):
            return self.reply(obj, 'signature')
        if (obj['protocol'] != PROTOCOL or obj['scope'] != SCOPE or obj['run_id'] != self.run_id
                or obj['kind'] not in ('step', 'snapshot')):
            return self.reply(obj, 'binding_scope')
        if (not valid_id(obj['attempt_id']) or not valid_id(obj['session_nonce'])
                or not valid_sha(obj['command_sha256']) or type(obj['seq']) is not int
                or type(obj['command_json']) is not str):
            return self.reply(obj, 'identity')
        if hashlib.sha256(obj['command_json'].encode('ascii')).hexdigest() != obj['command_sha256']:
            return self.reply(obj, 'command_sha')
        start, end = obj['issued_at_ms'], obj['expires_at_ms']
        if (type(start) is not int or type(end) is not int or not 0 < end - start <= 5000
                or not start <= now < end):
            return self.reply(obj, 'transport_expired')
        low, high = self.plan['window_ms']
        if not low <= now < high:
            return self.reply(obj, 'outside_authorized_window')
        normalized = canonical(dict(obj, mac=mac))
        old = self.seen.get(obj['attempt_id'])
        if old is not None:
            return old[1] if old[0] == normalized else self.reply(obj, 'attempt_conflict')
        if obj['kind'] == 'snapshot':
            if obj['command_json'] != canonical({'action': 'snapshot', 'n': obj['seq']}).decode('ascii'):
                return self.reply(obj, 'snapshot_command')
            if self.snapshots >= SNAPSHOT_LIMIT:
                return self.reply(obj, 'snapshot_limit')
            self.snapshots += 1
            try:
                result = self.snapshot()
            except Exception as exc:
                result = dict(status='snapshot_exception', error_type=type(exc).__name__)
            response = self.reply(obj, 'ok', result)
        else:
            index = obj['seq']
            steps = self.plan['steps']
            if not self.next_step <= index <= len(steps):
                return self.reply(obj, 'step_order')
            step = steps[index - 1]
            if obj['command_json'] != canonical(step).decode('ascii'):
                return self.reply(obj, 'step_not_authorized')
            # Consume the step before touching the broker: never run it twice,
            # even if the native call raises and its broker effect is unknown.
            self.next_step = index + 1
            try:
                result = self.run_step(C, step)
            except Exception as exc:
                result = dict(status='step_exception', error_type=type(exc).__name__)
            response = self.reply(obj, 'ok', result)
        self.seen[obj['attempt_id']] = (normalized, response)
        return response


def emit(status, **facts):
    row = dict(protocol=PROTOCOL, run_id=RUN_ID if valid_id(RUN_ID) else None,
               instance_mode=INSTANCE_MODE, status=status)
    row.update(facts)
    print(repr(row))


class ReportRing:
    def __init__(self, memory):
        self.memory = memory

    def __call__(self, row):
        payload = canonical(row)
        if len(payload) > REPORT_SLOT_SIZE - 4:
            payload = canonical(dict(kind=row.get('kind'), m_strRemark=row.get('m_strRemark'),
                                     oversize=True))
        self.memory.seek(REPORT_HEADER_OFFSET)
        count = int.from_bytes(self.memory.read(4), 'little')
        if count >= REPORT_LIMIT:
            self.memory.seek(REPORT_HEADER_OFFSET + 4)
            self.memory.write((1).to_bytes(4, 'little'))
            return
        slot = REPORT_SLOTS_OFFSET + count * REPORT_SLOT_SIZE
        self.memory.seek(slot)
        self.memory.write(len(payload).to_bytes(4, 'little') + payload)
        self.memory.seek(REPORT_HEADER_OFFSET)
        self.memory.write((count + 1).to_bytes(4, 'little'))


def finish(C, reason):
    state = _STATE
    if state is None or state['stopped']:
        return
    state['stopped'] = True
    cancelled = None
    try:
        cancelled = C.cancel_schedule_run('qsys601o_' + RUN_ID)
    except Exception as exc:
        emit('cancel_schedule_failed', error_type=type(exc).__name__)
    emit('finished', reason=reason, ticks=state['ticks'], next_step=state['executor'].next_step,
         cancel_schedule_result=cancelled)


def on_timer(C):
    state = _STATE
    if state is None or state['stopped']:
        return
    state['ticks'] += 1
    try:
        if state['ticks'] > MAX_TICKS or now_ms() >= state['deadline_ms']:
            finish(C, 'bound')
            return
        memory = state['memory']
        memory.seek(REQUEST_OFFSET)
        generation = int.from_bytes(memory.read(4), 'little')
        if generation == 0 or generation == state['last_generation']:
            return
        size = int.from_bytes(memory.read(4), 'little')
        if not 1 <= size <= 4000:
            raise ValueError('request frame size')
        raw = memory.read(size)
        memory.seek(REQUEST_OFFSET)
        if int.from_bytes(memory.read(4), 'little') != generation:
            return
        state['last_generation'] = generation
        reply = state['executor'].receive(C, raw, now_ms())
        if len(reply) > 2000:
            raise ValueError('response frame size')
        memory.seek(RESPONSE_OFFSET)
        memory.write(bytes(4))
        memory.seek(RESPONSE_OFFSET + 8)
        memory.write(reply)
        memory.seek(RESPONSE_OFFSET + 4)
        memory.write(len(reply).to_bytes(4, 'little'))
        memory.seek(RESPONSE_OFFSET)
        memory.write(generation.to_bytes(4, 'little'))
    except Exception as exc:
        emit('callback_failed', error_type=type(exc).__name__)
        finish(C, 'error')


def _active():
    state = _STATE
    return state if state is not None and not state['stopped'] else None


def order_callback(C, orderInfo):
    state = _active()
    if state is not None:
        try:
            state['executor'].record_order('callback', orderInfo)
        except Exception as exc:
            emit('order_callback_failed', error_type=type(exc).__name__)


def deal_callback(C, dealInfo):
    state = _active()
    if state is not None:
        try:
            state['executor'].record_deal('callback', dealInfo)
        except Exception as exc:
            emit('deal_callback_failed', error_type=type(exc).__name__)


def orderError_callback(C, orderArgs, errMsg):
    state = _active()
    if state is not None:
        try:
            state['executor'].record_error(orderArgs, errMsg)
        except Exception as exc:
            emit('order_error_callback_failed', error_type=type(exc).__name__)


def init(C):
    global _STATE, _RAN
    if _RAN:
        return
    _RAN = True
    memory = None
    stage = 'configuration'
    try:
        if ENABLED is not True:
            emit('disabled')
            return
        if (INSTANCE_MODE != 'authorized-order' or not valid_id(RUN_ID)
                or CLIENT_BUILD != '2.1.33.0' or re.fullmatch('[0-9]{6,20}', ACCOUNT_ID) is None
                or re.fullmatch('[0-9a-f]{64}', KEY_HEX) is None):
            raise ValueError('execution binding')
        plan = validate_plan(AUTHORIZED_PLAN, RUN_ID)
        api = {name: globals().get(name) for name in ('passorder', 'cancel', 'get_trade_detail_data')}
        if not all(callable(fn) for fn in api.values()):
            raise ValueError('native trading api unavailable')
        if (not callable(getattr(C, 'schedule_run', None))
                or not callable(getattr(C, 'cancel_schedule_run', None))
                or not callable(getattr(C, 'set_account', None))):
            raise ValueError('finite scheduler and account binding required')
        stage = 'open_named_memory'
        import mmap
        memory = mmap.mmap(-1, MAP_SIZE, tagname='Local\\qsys_authorized_order_' + RUN_ID,
                           access=mmap.ACCESS_WRITE)
        memory.seek(COORDINATOR_MARKER_OFFSET)
        marker = coordinator_marker(RUN_ID)
        if memory.read(len(marker)) != marker:
            raise ValueError('existing authorized-order coordinator marker required')
        executor = Executor(RUN_ID, bytes.fromhex(KEY_HEX), plan, api, ReportRing(memory))
        stage = 'set_account'
        C.set_account(ACCOUNT_ID)
        _STATE = dict(memory=memory, executor=executor, ticks=0, last_generation=0,
                      stopped=False, deadline_ms=now_ms() + (MAX_TICKS + 30) * 1000)
        stage = 'schedule_run'
        task_id = C.schedule_run(on_timer, datetime.now() + timedelta(seconds=2),
                                 MAX_TICKS, timedelta(seconds=1), 'qsys601o_' + RUN_ID)
        stage = 'publish_scheduled_marker'
        memory.seek(SCHEDULED_MARKER_OFFSET)
        memory.write(scheduled_marker(RUN_ID))
        emit('scheduled', task_id=task_id, steps=len(plan['steps']))
    except Exception as exc:
        emit('init_failed', stage=stage, error_type=type(exc).__name__)
        if _STATE is not None:
            finish(C, 'init_failed')
        elif memory is not None:
            memory.close()


def stop(C):
    finish(C, 'model_stop')


def handlebar(C):
    pass
