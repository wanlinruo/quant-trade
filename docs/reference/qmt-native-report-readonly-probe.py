# coding: utf-8
"""#601 QMT native account/report shape probe; disabled and read-only by default.

This candidate is for the formal model-trading container only.  It observes
field presence and Python types, never field values.  Enabling it reads real
account facts and therefore requires a separate maintainer authorization.
"""

from datetime import datetime


ENABLED = False
CLIENT_BUILD = ""
PROBE_DATE = ""
RUN_ID = ""
ACCOUNT_TYPE = "STOCK"
READ_ONLY_ACK = ""
QUERIES_ENABLED = False
CALLBACKS_ENABLED = False
CALLBACK_ACK = ""

_EXPECTED_BUILD = "2.1.33.0"
_EXPECTED_ACK = "READ_REAL_ACCOUNT_FACTS_WITHOUT_TRADING"
_EXPECTED_CALLBACK_ACK = "OBSERVE_NATIVE_CALLBACKS_WITHOUT_TRADING"
_PROTOCOL = "qsys.qmt-native-report-shape.v1"
_QUERY_KINDS = ("ACCOUNT", "POSITION", "ORDER", "DEAL")
_MAX_QUERY_ROWS = 5000
_MAX_CALLBACK_OBSERVATIONS = 4
_CONFIG_OK = False
_CALLBACK_OBSERVATIONS = 0

_FIELDS = {
    "ACCOUNT": (
        "m_strAccountID", "m_strAccountKey", "m_nBrokerType", "m_strStatus",
        "m_strTradingDate", "m_dBalance", "m_dAvailable", "m_dFrozenCash",
        "m_dInstrumentValue", "m_dAssureAsset",
    ),
    "POSITION": (
        "m_strAccountID", "m_strAccountKey", "m_strExchangeID",
        "m_strInstrumentID", "m_nVolume", "m_nCanUseVolume",
        "m_nFrozenVolume", "m_dOpenPrice", "m_dMarketValue",
        "m_dInstrumentValue", "m_strTradingDay",
    ),
    "ORDER": (
        "m_strAccountID", "m_strAccountKey", "m_strExchangeID",
        "m_strInstrumentID", "m_strOrderSysID", "m_strOrderRef",
        "m_strRemark", "m_nOffsetFlag", "m_nVolumeTotalOriginal",
        "m_nVolumeTraded", "m_nVolumeTotal", "m_nOrderStatus",
        "m_nErrorID", "m_strErrorMsg", "m_strCancelInfo",
        "m_strInsertDate", "m_strInsertTime",
    ),
    "DEAL": (
        "m_strAccountID", "m_strAccountKey", "m_strExchangeID",
        "m_strInstrumentID", "m_strTradeID", "m_strOrderSysID",
        "m_strOrderRef", "m_strRemark", "m_nOffsetFlag", "m_dPrice",
        "m_nVolume", "m_strTradeDate", "m_strTradeTime",
        "m_dCommission", "m_dComission", "m_dComssion",
    ),
}


def _emit(check, status, **facts):
    row = dict(protocol=_PROTOCOL, run_id=RUN_ID, check=check, status=status)
    row.update(facts)
    print(repr(row))


def _validate_config():
    if ENABLED is not True:
        raise ValueError("probe disabled")
    if CLIENT_BUILD != _EXPECTED_BUILD:
        raise ValueError("unexpected client build")
    if len(PROBE_DATE) != 8 or not PROBE_DATE.isdigit():
        raise ValueError("explicit probe date required")
    datetime.strptime(PROBE_DATE, "%Y%m%d")
    if PROBE_DATE != datetime.now().strftime("%Y%m%d"):
        raise ValueError("probe date mismatch")
    if len(RUN_ID) != 32 or any(c not in "0123456789abcdef" for c in RUN_ID):
        raise ValueError("explicit run id required")
    if ACCOUNT_TYPE != "STOCK":
        raise ValueError("stock account type required")
    if type(QUERIES_ENABLED) is not bool:
        raise ValueError("explicit query mode required")
    if type(CALLBACKS_ENABLED) is not bool:
        raise ValueError("explicit callback mode required")
    if QUERIES_ENABLED == CALLBACKS_ENABLED:
        raise ValueError("exactly one probe mode required")
    if QUERIES_ENABLED is True:
        if READ_ONLY_ACK != _EXPECTED_ACK:
            raise ValueError("explicit real-account read acknowledgement required")
        if CALLBACK_ACK:
            raise ValueError("callback acknowledgement present in query mode")
    else:
        if READ_ONLY_ACK:
            raise ValueError("query acknowledgement present in callback mode")
        if CALLBACK_ACK != _EXPECTED_CALLBACK_ACK:
            raise ValueError("explicit native callback acknowledgement required")


def _bound_account():
    value = globals().get("account")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("formal model account binding required")
    return value


def _field_type(row, field):
    try:
        return type(getattr(row, field)).__name__
    except Exception:
        return "attribute_error"


def _summarize(kind, source, rows, bound_account):
    if not isinstance(rows, (list, tuple)):
        _emit(kind.lower(), "unexpected_type", source=source,
              returned_type=type(rows).__name__, normalization_performed=False)
        return
    if len(rows) > _MAX_QUERY_ROWS:
        _emit(kind.lower(), "rejected", source=source,
              reason="row_limit", row_count_emitted=False,
              normalization_performed=False)
        return
    if not rows:
        _emit(kind.lower(), "empty_unverified", source=source, nonempty=False,
              row_count_emitted=False, account_match=None,
              normalization_performed=False)
        return

    expected = _FIELDS[kind]
    coverage = {}
    type_names = {}
    account_matches = []
    for field in expected:
        present = [row for row in rows if hasattr(row, field)]
        coverage[field] = (
            "none" if not present else "all" if len(present) == len(rows) else "partial"
        )
        type_names[field] = sorted(set(_field_type(row, field) for row in present))
    for row in rows:
        if hasattr(row, "m_strAccountID"):
            try:
                account_matches.append(getattr(row, "m_strAccountID") == bound_account)
            except Exception:
                account_matches.append(False)

    _emit(
        kind.lower(), "shape_observed", source=source, nonempty=True,
        row_count_emitted=False,
        object_types=sorted(set(type(row).__name__ for row in rows)),
        field_coverage=coverage, field_types=type_names,
        missing_every_row=sorted(field for field in expected if coverage[field] == "none"),
        partial_fields=sorted(field for field in expected if coverage[field] == "partial"),
        account_match=(all(account_matches) if account_matches else None),
        raw_values_emitted=False, normalization_performed=False,
    )


def _query_once(bound_account):
    query = globals().get("get_trade_detail_data")
    if not callable(query):
        raise ValueError("native query unavailable")
    calls = 0
    for kind in _QUERY_KINDS:
        try:
            calls += 1
            rows = query(bound_account, ACCOUNT_TYPE, kind)
            _summarize(kind, "query", rows, bound_account)
        except Exception as exc:
            _emit(kind.lower(), "query_error", source="query",
                  error_type=type(exc).__name__, raw_values_emitted=False,
                  normalization_performed=False)
    return calls


def init(ContextInfo):
    global _CONFIG_OK, _CALLBACK_OBSERVATIONS
    _CONFIG_OK = False
    _CALLBACK_OBSERVATIONS = 0
    try:
        _validate_config()
        bound_account = _bound_account()
        if CALLBACKS_ENABLED is True:
            setter = getattr(ContextInfo, "set_account", None)
            if not callable(setter):
                raise ValueError("set_account unavailable")
            setter(bound_account)
    except Exception as exc:
        _emit("configuration", "rejected", error_type=type(exc).__name__,
              account_calls=0, trading_calls=0)
        return

    _CONFIG_OK = True
    _emit("configuration", "accepted", client_build=CLIENT_BUILD,
          probe_date=PROBE_DATE, account_type=ACCOUNT_TYPE,
          current_queries_requested=QUERIES_ENABLED,
          callback_subscription_requested=CALLBACKS_ENABLED,
          account_value_emitted=False)
    query_calls = _query_once(bound_account) if QUERIES_ENABLED is True else 0
    _emit("scope", "incomplete", query_calls=query_calls, account_reads=query_calls,
          callback_observations=0, trading_calls=0, cancel_calls=0,
          history_reads=0, raw_values_emitted=False,
          normalization_performed=False, authorizes_trading=False)


def _callback(kind, row):
    global _CALLBACK_OBSERVATIONS
    if (not _CONFIG_OK or CALLBACKS_ENABLED is not True or
            _CALLBACK_OBSERVATIONS >= _MAX_CALLBACK_OBSERVATIONS):
        return
    _CALLBACK_OBSERVATIONS += 1
    try:
        bound_account = _bound_account()
        _summarize(kind, "callback", [row], bound_account)
    except Exception as exc:
        _emit(kind.lower(), "callback_error", source="callback",
              error_type=type(exc).__name__, raw_values_emitted=False,
              normalization_performed=False)


def account_callback(ContextInfo, accountInfo):
    _callback("ACCOUNT", accountInfo)


def order_callback(ContextInfo, orderInfo):
    _callback("ORDER", orderInfo)


def deal_callback(ContextInfo, dealInfo):
    _callback("DEAL", dealInfo)


def position_callback(ContextInfo, positionInfo):
    _callback("POSITION", positionInfo)


def handlebar(ContextInfo):
    pass
