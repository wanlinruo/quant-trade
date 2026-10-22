# Redis5 只读能力隔离（#488 / #330）

## 范围与现状

2026-10-22实测Windows Redis5.0.14.1不支持ACL、无密码、仅loopback；Linux仅以
127.0.0.1:6379 SSH隧道接入。只使用GET的代码不构成权限隔离。

本次五个账号：Web、health-observer、daily-digest、reboot-drill经本机AF_UNIX
入口GET；service-checks无Redis依赖，只封锁直连。其余非交易账号不在本轮验收内。
Live/Paper/Shadow继续使用原协议；盘前精确SET入口保持。没有升级/重启Redis、
隧道或Windows，没有修改交易开关/资金，没有生产写入/故障探针。

| 消费者 | 允许GET（由现场最小配置复核） |
|---|---|
| Web | control:kill，state:account，state:heartbeat:agent/core，state:market_overview，state:market_schedule，state:price_limits_check:YYYYMMDD |
| observer | control:kill，state:heartbeat:agent/core/market/paper |
| digest | control:kill，state:heartbeat:agent |
| reboot-drill | state:heartbeat:agent/market |
| service-checks | 无 |

Web的account/watch_sources来自WebConfig，其余流名来自实际deploy.toml；自定义流名必须
重新盘点并显式配置，不以默认前缀代替现场事实。日期后缀须为真实日历日期，不开放任意pattern。

## 控制面与协议

`src/qsys/live/redis_readonly_gateway.py`仅依赖标准库，从已合并精确SHA安装为
root拥有的`/usr/local/libexec/qsys-redis-reader.py`。这是基础安全控制面，与既有固定发布
入口相同，不使用共享checkout/venv；不是第16个业务组件，不把它算进独立业务部署数量。

`qsys-redis-reader.socket`由root创建`/run/qsys-redis-reader.sock`，root:qsys-observers
0660。`qsys-redis-reader.service`使用专属无登录/无sudo用户，不加入observers，读取
root:qsys-redis-reader0640的`/etc/qsys/redis-reader/policy.json`。

入口只接受一行UTF8 JSON：`{"protocol":1,"key":"…"}`，限制4096字节，重复/额外字段、
命令、调用方UID均拒绝。SO_PEERCRED识别内核真实UID，各自精确白名单。后端固定RESP2 GET，
不接受外部命令/地址/库参数；原字符串和null不改写。后端错误有单独error响应，不伪装缺失。
单值上限4MiB、4个并发连接、后端2秒/socket3秒超时，socket排队32；客户端有限超时且不重试。

四个CLI仅在专属unit显式设置`QSYS_REDIS_READER_SOCKET`时启用；入口不可用不回退直连。
未设置时保持原环境行为，用于旧制品和隔离测试兼容。其余写者factory不变。

独立`inet qsys_redis_readonly`表在OUTPUT按UID拒绝TCP目的6379（IPv4/IPv6、已有连接均适用），
不封锁整个loopback，不改其他表。更新先nft --check，再单事务替换本表；不flush ruleset。
netguard启动在五组件之前，组件Requires/After该服务；四读取组件另Requires socket。
root策略的blocked_uids分阶段扩展，拒绝把root/交易者/隧道/未知账号写入已审查集合。

残余信任：root、入口服务、共享Redis与SSH隧道仍受信任；这不是Redis原生ACL，不保护root被攻陷、
跨UID传递已打开FD、其他有完整凭据的写者主动代理请求。协议/端口门禁保护本轮五个UID的直接能力。

## 安装与发布顺序

1. main已合并且CI/测试部署成功后，Linux只从GitHub获取精确SHA；归档原units、策略、文件hash、
   UID、关键进程PID/启动时间/NRestarts、现有nft表清单。先ls -la检查路径/软链接，不覆盖未知对象。
2. 建立专属system user/group qsys-redis-reader，不授予登录、sudo、业务目录访问。
   建立root:qsys-redis-reader0750策略目录。按四消费者当前配置生成root策略，核对endpoint确为
   127.0.0.1:6379/db0/no-password/no-TLS；不把完整生产配置复制给入口。
   policy schema示例（UID必须用现场pwd解析，不能照抄数字）：
   `{"protocol":1,"redis":{"host":"127.0.0.1","port":6379,"db":0},"readers":{"<web-uid>":{"keys":["control:kill"],"date_prefixes":[]}},"blocked_uids":[<checks-uid>]}`。
3. 使用该SHA的gateway脚本`check --policy …`，然后`install --policy …`安装固定代码和三个units。
   install不启动/停止服务，不改策略；同名.new存在会拒绝，不能覆盖未知临时对象。
   systemd-analyze verify后daemon-reload，enable --now netguard.service与reader.socket。
   初始仅service-checks UID进入blocked_uids，旧消费者仍可读。检查ruleset保持其他表。
4. 先用四个真实UID调用新入口GET并对比旧GET（仅报告一致性/hash，不输出账户/心跳值）。
   错误key/额外字段/冒充UID在入口拒绝；所有写命令探针只在隔离服务进行，不向生产发写入。
5. 逐个按正常component-deploy dry-run/formal发布四消费者。workflow最终success、GitHub
   Deployment success、节点实际validate_record三方一致后，验证当前CLI/页面/读取结果；再将
   该UID原子加入root策略blocked_uids，reload netguard应用新规则（不能restart其Requires依赖以免连带停止消费者）。每步验证该UID
   直连6379失败且入口仍可读、TLS/SSH必要读取可用、交易者/隧道PID与重启计数保持。
   reload策略须重启入口service，socket保持；先确认全部新白名单是旧集合超集。
6. service-checks发布manifest新依赖并运行实际检查；digest不发送测试邮件，reboot-drill只读
   probe/既有dry-run，不执行重启。自然timer验收单记，不冒充人工兼容验证。
7. 逐项证据留`/data/qsys/var-deployments/issue488-*`并回写Issue/HANDOFF；未迁移UID不称完成。

正常异常向前修复；保留原制品/策略备份和首次失败证据，不做生产legacy回切或删除备份。
服务启动依赖/资源/AF_UNIX权限和实际节点Python版本须在切消费者前核验；node切换不替代发布后验。

机制依据：[nftables手册](https://www.netfilter.org/projects/nftables/manpage.html)、
[systemd socket activation](https://www.freedesktop.org/software/systemd/man/256/systemd.socket.html)。
本文件是实施契约；生产完成状态以#488最新评论与节点证据为准，不能由代码合入推定。
