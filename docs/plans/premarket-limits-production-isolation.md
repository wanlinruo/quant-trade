# 盘前涨跌停独立生产迁移（#487 / #330）

## 边界

`qsys-premarket-limits` 使用私有主组，仅附加 `qsys-observers`。只写
`price_limits_daily`、`suspend_daily`、`price_limit_daily` 和原有自身运行记录目录；
Warehouse 根、其余表、共享 checkout、完整生产配置和交易状态不可写。
全局单写锁以只读描述符取得排他 flock，不扩大锁文件写权限。
自身状态目录0770、私有主组及Ubuntu named ACL兼容旧入口，observers只读，
先用真实 GNU install 和原子替换核验，不递归变更旧文件owner。

采集配置 `/etc/qsys/components/premarket-limits/deploy.toml` 仅含schema_version、
Tushare token及按当前生产入口配置解析得到的 `streams.price_limits`。保留三次定时触发、
停复牌快照退化保护、涨跌停完整度门禁、09:25截止、canonical写后对账和缺价买单拒绝。
初始资源上限 MemoryHigh768M/Max1G、CPU50%、单线程，DuckDB256MiB/spill512MiB，
以隔离实跑证据复核。timer不因等待发布被暂停，避免无谓维护告警。

## Redis 5 限权发布

节点实测Redis 5.0.14.1，RESP2；`ACL`返回unknown command，不能假设存在Redis6 ACL。
进一步实测当前Redis经127.0.0.1:6379的SSH隧道访问，没有密码。
因此采集unit必须以IPAddressDeny阻断127.0.0.1/32、::1/128及IPv4映射地址，
实际沙箱内验证IPv4/IPv6映射直连拒绝；本机SSHD改经127.0.0.2调用受限入口。
当前隧道只监听127.0.0.1，不改绑定或SSH隧道，不影响127.0.0.53 DNS及外部Tushare。
禁止把无密码配置误称凭据隔离；网络门禁和私有系统账号同时生效才构成边界。
不升级/重启Redis，不修改通用交易Transport。
复用系统SSHD的本机 forced-command，没有新增常驻服务。

- 采集身份持有专属loopback SSH key，主机key从真实节点公钥固定至known_hosts。
- 发布身份 `qsys-price-limits-publisher` 使用私有组、禁用密码、无sudo/observers权限。
  home/.ssh/authorized_keys均root持有且该身份不可写；仅新增专用授权行，
  `from="127.0.0.1",restrict,command="..."`，不改个人key或全局SSH配置。
- 固定命令为 `timeout 20 prlimit --as=268435456 --cpu=10 --nofile=64
  /opt/qsys/components/premarket-limits/current/venv/bin/python -I -B
  -m qsys.data.price_limits_publisher --config <发布身份专属配置>`。
  配置root:发布私有组0640，只有Redis连接参数和固定price_limits prefix，采集账号不可读。
  不通过sudo穿越 `NoNewPrivileges`，客户端unit的沙箱保持启用。
- 只接受 `SSH_ORIGINAL_COMMAND=publish-price-limits-v1`，拒绝shell/SFTP/其他命令。
  信封只含protocol=1/day/value，最大4MiB，拒绝重复/额外字段、无效日期、证券代码、
  非二元价格数组、bool/NaN/Inf；最多20000证券。允许接口原值null/0/负数，不自算/填充。
- key完全由发布身份的固定prefix及校验后的日期生成，不接受caller指定任意Redis key。
  仅执行SET，原始JSON payload字节保持，ack绑定日期/条数/SHA256。
  单请求20秒/256MiB地址空间/10秒CPU/64fd兜底，客户端25秒超时；错误不回显秘密配置。
  发布身份对底层Redis具有默认用户权限，是Redis5的残余可信边界；root持有的程序、
  私有配置、forced-command及采集unit网络限制共同限定被调用能力，
  不能宣称原生Redis ACL已实现或将其概括为密码隔离。

legacy不传publisher参数时仍用既有RedisTransport；其他已发布组件与Windows无需联合升级。
新旧路径的key、JSON值、涨跌停原值和消费者解释保持一致。

## 验收顺序

先备份旧状态文件真实内容及ACL/hash、配置指纹和关键peer身份，创建精确三表写权限，
用OS与实际systemd沙箱验证必要读写、无关写/完整配置读拒绝、只读描述符flock。
当前生产消费者以各自账号及venv验证新记录/数据可读且不重复，文件不可写。

真实预生产使用明确已收盘日（例如2026-10-21）和隔离Warehouse，生产Warehouse只读；
另建固定测试prefix发布配置及独立验证key，真实Redis只写测试namespace，不覆盖生产缓存。
检查payload与源值一致、旧版消费者可解释、任意key/命令/流/shell/SFTP拒绝，发布配置不可读。
不能用历史日期在生产重跑（盘前pre_close=null会覆盖17点回填），午夜不默认采当日。

正常精确SHA CI→测试部署→代行逐文件自审PR→合并→final artifact→dry-run→正式发布；
workflow/Deployment/节点实际validate_record三方success后再验收现场。
生产业务采用08:58/09:15/09:24自然运行，不将测试namespace、休市skip或控制面attestation
冒充真实交易日全量验收。保留原始备份、旧release和历史；正常异常向前修复。
