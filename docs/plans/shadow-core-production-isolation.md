# Shadow Core 独立休眠部署（#493 / #330）

## 生产状态保持

当前生产Shadow为inactive/MainPID0、enabled，共享配置mode=live/orders_enabled=true。
Shadow CLI只允许shadow/false，因此原停用门禁在读取Redis之前生效。
生产私有profile保留原mode/orders字段，**不为验收改成shadow/false、不启动生产Shadow、
不改变enabled或全局交易开关**。制品current、单元及运行账号切换不等于业务激活；
未来启用仍需明确授权、独立的消费者路径切换与真实业务验收。

实际策略承袭模拟账户；Workbench相对路径按原工作目录解析成等价绝对路径，源码、参数、
血缘保持。fallback策略文件与main字节相同（SHA256 3f476cdb…），只调整到制品内绝对路径，
不重填参数/阈值。preflight中的策略文件存在性不能冒充承袭源码与参数对账，后者单独验证。

## 状态、权限与控制

私有账号qsys-shadow-core，只附加observers；只写var/shadow-core及var/alerts/shadow-core。
策略快照、SQLite、私有journal、锁与本地SHADOW_KILL均在前者内。
Workbench、市场Warehouse只读，其他状态写及完整配置读拒绝。目录0770配Ubuntu写ACL、
observers只读ACL及default ACL；真实GNU install后核验，不能用default ACL替代access ACL。
新state文件保留Ubuntu可写的有效ACL mask，不递归改旧文件owner。

旧shadow-state.sqlite3（114688字节，SHA256 0f47789c…）保持原位置和内容，
用SQLite backup API生成真实一致性副本，按表逻辑行对账后复制到新私有生产状态及验证状态。
旧锁/历史保留。原本本地SHADOW_KILL不存在，新路径也保持不存在，不操作kill/resume或Redis kill值。
当前共享kill.py已与main字节相同，能根据固定component drop-in定位新文件；只读验证路径和
维护者权限，不触发任何停机闸操作。C1扫描增加src/qsys/agent，覆盖实际单实例锁依赖。

最小配置只含实际需要的Redis/streams、Tushare日历、risk、strategy、Shadow/ops通知及原安全门禁。
私有配置root:组件组0640，配置与release不可由运行身份修改。Redis5强制权限残余仍由#488承接。
初始MemoryHigh1G/Max2G/Swap1G、CPUWeight100、单线程及DuckDB256MiB/spill1024MiB，
以隔离初始化/心跳实测记录资源，不把它当自然交易事件性能验收。

## 真正隔离的预生产

隔离profile才使用shadow/false、空通知目标，独立state/workspace/journal/alerts/lock/kill文件。
不能仅改StreamConfig：运行时有硬编码shadow:control:kill和shadow:intents。
显式--validation-namespace启用ValidationTransport，所有9类传输操作统一加
component-validation:前缀，包括硬编码地址、消费组所在stream、heartbeat与quote地址。
默认生产不传参数，原协议不变；前缀只允许component-validation:范围，不能当原生Redis ACL。
单元测试覆盖真实KillSwitch及stream投递/重置/确认、原value保留、旧协议和生产姿态提前拒绝。

初始化需要新鲜AccountSnapshot。预生产仅把现有**不含账号标识**的账户快照及agent/market
心跳只读镜像到测试前缀，保留原数值和时间戳；不配置真实账户、不连接真实委托流、不编造
新鲜时间。完整快照不进入Git/Issue/Actions日志。临时镜像与Shadow验证均正常停止并留记录。
旧持仓未被验证行情覆盖时明确列未覆盖，不清空持仓、不猜价；至少验收真实策略血缘初始化、
私有状态可用、持续heartbeat、进程身份与资源，不能把初始化当完整策略业务运行。

## 消费者与发布分层

当前Web处于Live模式，实际选Live状态；日报仍读旧Shadow快照。生产Shadow不启动，
旧数据保持，当前读路径不被强行改向空的新journal。用当前Web/日报实际账号及venv显式读取
新路径验证schema/权限，不冒称已切换生产消费路径；未来启动Shadow前必须联合切读路径。
日报#490已兼容Shadow告警目录，旧flat告警不复制、不丢失。

稳定SHA CI→测试部署/控制面attestation（如实区分）→代行自审PR→最终制品→真实隔离验证→
dry-run→正式发布；workflow/Deployment/节点实际validate_record三方success后，确认current、
实际单元身份、inactive/enabled与原生产门禁、原状态/全局配置/kill值及所有关键peer保持。
正常异常向前修复，不做生产legacy回切、故障注入或真实交易激活。
