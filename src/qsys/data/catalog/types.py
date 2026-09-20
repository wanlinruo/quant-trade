"""指标字典的类型。

字典是**代码**不是文档，因为只有代码能被测试守住「schema 加了列而字典
没登记」。三个分类判据（见 spec §3.1）在这里落成构造期约束：类型与血缘
不匹配直接抛错，不给「先写着回头再改」留口子。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

# 含义待查的哨兵。写 UNKNOWN 是合规行为，编一句听起来合理的中文才是违规。
# 页面按 `is UNKNOWN` 精确识别并标红，所以必须共用这一个对象。
UNKNOWN = "UNKNOWN(待查)"


class Kind(StrEnum):
    """spec §3.1：直取看能否指到源字段；派生看有无自由参数；
    有自由参数或口径选择的是因子。"""

    DIRECT = "direct"
    DERIVED = "derived"
    FACTOR = "factor"


class Validity(StrEnum):
    """这个值在什么范围内成立。防的是拿点观测去填历史（结转）。"""

    SESSION = "session"  # 该交易日成立
    POINT = "point"      # 仅观测日成立，如 name_snapshots.name
    SPAN = "span"        # 区间内成立，如 name_history 的名称区间


class MetricStatus(StrEnum):
    """这个**指标**是在产、已冻结、还是运行时日志。"""

    ACTIVE = "active"
    FROZEN = "frozen"
    OPERATIONAL = "operational"


class SourceStatus(StrEnum):
    """这个**源**还能不能取到新数据。与 MetricStatus 取值部分同名但语义
    不同，故意分成两个枚举——bars_daily 是 Metric.FROZEN（我们的决定）
    而其源 mootdx.kline 是 Source.DEAD（上游的事实）。"""

    ACTIVE = "active"
    DEAD = "dead"
    FROZEN = "frozen"


class Access(StrEnum):
    API = "api"            # 自动接口
    FILE = "file"          # 人工转存文件
    REALTIME = "realtime"  # 实时自采，漏一天永久缺一天


class DatasetLayer(StrEnum):
    """数据集所处的稳定架构层，而不是供应商或存储介质。"""

    RAW = "raw"
    CANONICAL = "canonical"
    REALTIME = "realtime"
    DERIVED = "derived"
    OPERATIONAL = "operational"


class DatasetRole(StrEnum):
    DIMENSION = "dimension"
    FACT = "fact"
    EVENT = "event"
    SNAPSHOT = "snapshot"
    FACTOR = "factor"
    LOGICAL = "logical"


class DatasetStatus(StrEnum):
    ACTIVE = "active"
    FROZEN = "frozen"
    OPERATIONAL = "operational"


class DependencyKind(StrEnum):
    PASSTHROUGH = "passthrough"
    JOIN = "join"
    AGGREGATION = "aggregation"
    WINDOW = "window"
    PUBLISH = "publish"


class JoinCardinality(StrEnum):
    ONE_TO_ONE = "1:1"
    ONE_TO_MANY = "1:N"
    MANY_TO_ONE = "N:1"
    UNKNOWN = "unknown"


class DependencyRequirement(StrEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"


@dataclass(frozen=True, slots=True)
class Dataset:
    """表级契约。

    ``schema_id`` 指向 :mod:`qsys.data.schema` 中的物理契约；逻辑视图为
    ``None``。不能安全确认的粒度或入口必须写 ``UNKNOWN``，不能靠默认值
    隐去。
    """

    id: str
    name: str
    purpose: str
    layer: DatasetLayer
    role: DatasetRole
    status: DatasetStatus
    grain: str
    primary_key: tuple[str, ...]
    time_columns: tuple[str, ...]
    partition_keys: tuple[str, ...]
    sort_keys: tuple[str, ...]
    schema_id: str | None
    schema_version: int
    compatibility: str
    builder: str
    update_mode: str
    finalized_at: str
    lifecycle: str
    sources: tuple[str, ...]
    consumers: tuple[str, ...]
    health_policy: str | None
    physical: bool = True

    def __post_init__(self) -> None:
        if not self.id or not self.name or not self.purpose:
            raise ValueError("Dataset 的 id/name/purpose 不得为空")
        if self.physical != (self.schema_id is not None):
            raise ValueError(f"{self.id}: physical 与 schema_id 不一致")
        if self.schema_version < 1:
            raise ValueError(f"{self.id}: schema_version 必须大于 0")
        if len(set(self.primary_key)) != len(self.primary_key):
            raise ValueError(f"{self.id}: 主键列重复")


@dataclass(frozen=True, slots=True)
class DatasetDependency:
    """跨数据集关系契约；无法确认的连接键/基数必须显式写 UNKNOWN。"""

    upstream: str
    downstream: str
    kind: DependencyKind
    join_keys: tuple[tuple[str, str], ...]
    cardinality: JoinCardinality
    requirement: DependencyRequirement
    allowed_lag: str
    metrics: tuple[str, ...]
    health_policy: str | None
    note: str = ""

    def __post_init__(self) -> None:
        if self.upstream == self.downstream:
            raise ValueError("DatasetDependency 必须是跨表关系")
        if not self.allowed_lag:
            raise ValueError(
                f"{self.upstream}->{self.downstream}: allowed_lag 不得为空"
            )
        if (
            self.cardinality is not JoinCardinality.UNKNOWN
            and not self.join_keys
            and self.kind is DependencyKind.JOIN
        ):
            raise ValueError(
                f"{self.upstream}->{self.downstream}: JOIN 基数已知时必须登记键"
            )


@dataclass(frozen=True, slots=True)
class Uncovered:
    """显式声明「这条指标没有验证规则」及其理由。

    `checks=()` 不允许：空元组分不清「忘了挂」和「确实不需要」，
    而这两者在页面上必须能区分。
    """

    reason: str


def UNCOVERED(reason: str) -> Uncovered:
    return Uncovered(reason)


@dataclass(frozen=True, slots=True)
class Source:
    id: str                    # "tushare.daily_basic"
    provider: str              # "Tushare Pro"
    api: str                   # "daily_basic"
    access: Access
    status: SourceStatus
    credential: str | None = None
    tier: str | None = None    # 权限门槛，如 "2000 积分"
    acquired: str | None = None
    cadence: str | None = None
    caveat: str | None = None  # 已知的时点/口径坑
    expires: date | None = None  # C5 凭据过期告警用


@dataclass(frozen=True, slots=True)
class Direct:
    """直取血缘：指到唯一的 (源, 字段)，最多一次纯乘法换算。

    抽不出这个三元组、代码里有条件分支的字段**本来就不该叫直取**，
    归 DERIVED（spec §3.1 的反向作用）。
    """

    source_id: str
    field: str
    multiplier: float = 1.0
    note: str | None = None


@dataclass(frozen=True, slots=True)
class Computed:
    """派生/因子血缘。`params` 非空基本意味着它是 FACTOR 而非 DERIVED。"""

    upstream: tuple[str, ...]  # metric id
    formula: str               # 人能读的公式
    params: tuple[tuple[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class Metric:
    id: str
    kind: Kind
    validity: Validity
    status: MetricStatus
    meaning: str
    lineage: Direct | Computed
    checks: tuple[str, ...] | Uncovered
    known_issues: tuple[str, ...] = ()
    ext_consumers: tuple[str, ...] = ()  # 非指标消费方：策略 Gate / 回测 / 页面
    landed: bool = True  # False = 策略侧不落盘的决策量

    def __post_init__(self) -> None:
        if self.kind is Kind.DIRECT and not isinstance(self.lineage, Direct):
            raise ValueError(f"{self.id}: DIRECT 必须带 Direct 血缘")
        if self.kind is not Kind.DIRECT and not isinstance(self.lineage, Computed):
            raise ValueError(
                f"{self.id}: {self.kind.name} 必须带 Computed 血缘，不得直接指源字段"
            )
        if isinstance(self.checks, tuple) and not self.checks:
            raise ValueError(f"{self.id}: checks 不得为空元组，请写 UNCOVERED(理由)")

    @property
    def dataset(self) -> str:
        return self.id.split(".", 1)[0]

    @property
    def column(self) -> str:
        return self.id.split(".", 1)[1]
