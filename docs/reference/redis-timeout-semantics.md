# Redis 超时与失败语义

公共 `RedisTransport.from_config` 只用于短命令。默认建连上限和单次命令上限均为
5 秒，可在 `[redis]` 中分别用 `connect_timeout_seconds`、
`command_timeout_seconds` 调整。两者必须大于 0；超时不在传输层隐式重试，异常交给
业务循环处理，下一次调用由 redis-py 连接池重新建连。

| 调用类别 | 当前操作 | 超时后的业务语义 |
|---|---|---|
| 短读取 | `GET`、`XPENDING`、`XAUTOCLAIM`、非阻塞 `XREADGROUP` | 本轮失败；常驻循环记录故障并在下一轮重试，监控页面降级为未知 |
| 发布/状态写入 | `XADD`、`SET`、`DEL`、`XACK` | 向调用方抛错；不得假定写入未发生，Streams 消费依靠 PEL 与业务幂等恢复 |
| 控制面 | kill switch、撤单指令、消费组管理 | 向调用方抛错；实盘/代理按现有 fail-closed 逻辑落本地闸或告警 |
| Streams 轮询 | `XREADGROUP` 不带 `BLOCK` | 空队列立即返回，由调用方的主循环控制轮询间隔，不占住心跳线程 |

不要在公共客户端上增加 `BLOCK`。若以后确需 Redis Streams 长轮询，必须创建独立
客户端，其读取超时应明确大于 `BLOCK` 时长，并保证心跳由另一线程或客户端发送。
这可以避免为长轮询放宽公共超时后重新引入半开连接永久等待。

## 故障与恢复

- Redis 不可达或连接建立过慢：在连接上限内抛错。
- 已建立连接半开或服务端响应过慢：在命令上限内抛错。
- 故障解除：下一轮业务调用重新取连接并恢复；常驻服务无需重启。
- 告警：agent、paper、shadow、live、market gateway 已在各自主循环记录失败；
  health observer 将 `redis_error` 提升为 critical，外部看门狗使用独立的更短探测超时。

故障演练时应分别验证拒绝连接、接受但不响应、恢复响应三种状态，并观察心跳不会因
单次 Redis I/O 永久停止。生产发布后至少覆盖一个交易时段，确认不存在正常短命令
被误判超时，也没有因连接异常造成常驻服务永久失联。
