# QMT分钟本地缓存读取（#550 / #478 / #540）

## 已实测事实：2026-10-30 14:29

`scripts/probe_qmt_local_minutes.py`在实际Windows Python3.11.9、QMT PID5340/58610运行；
源码ddff87e，helper PID1072，4.141秒完成，客户端PID保持。下载/订阅/账户/交易调用均0。
原生观测摘要见[qmt-local-minute-probe-20261030.json](qmt-local-minute-probe-20261030.json)。

- `get_local_data`真实返回`{symbol: DataFrame}`，列为time/open/high/low/close/volume/amount，
  索引为YYYYMMDDHHMMSS。显式count=-1、dividend_type=none；毫秒time按UTC+8与索引逐行一致。
- 沪深京、科创及已退市301139.SZ的5组有缓存窗口各482行，单日拼接与区间逐值完全相同。
  实际保留的零量行数分别4、2、3、80、63；不以零量判定填充。
- 8组样本的本地fill_data=True/False结果相同。这只证明这些缓存窗口，不能推断所有稀疏、
  停牌、上市前窗口都相同，也不能把缓存没有513100.SH九月数据当作该股无行情。
- 301139.SZ 10-26/27 RPC仍全部零价；本地读回真实0.68/0.66等价格，复现#550。
  其10-28、600000.SH 10-25及513100.SH九月样本本地均0行，无填值或下载。
- 首轮RPC与本地DataFrame.equals不相等，还混有dtype等结构因素；**不据此认定正常股票
  数值全有差异**。补充探针6626a3b分离逐字段精确差异与dtype；结果取得后另记。

官方资料：[行情模块](https://dict.thinktrader.net/nativeApi/xtdata.html)和
[股票数据](https://dict.thinktrader.net/dictionary/stock.html)均描述fill_data填充开关，
但前者本地接口返回形状文字与后者及实际SDK不一致，容器按实际探针适配，不能猜测兼容。

## 15:11补充：逐值比较结果

6626a3b探针helper PID456完成8组对照，仍无下载/订阅/账户/交易调用，客户端5340保持。
[完整摘要](qmt-local-minute-probe-r2-20261030.json)保留逐字段差异数和最大绝对差值。
有数据的5组索引与dtype相同，但普通股票确有极小Float64差别：OHLC最大
5.684341886080802e-14、amount最大7.450580596923828e-9，volume全部逐值一致。
这不是容器dtype造成；本地原值照实保留，不用舍入或容差把两API标作逐值相同。
301139.SZ仍有全部482根OHLC真实差异，原RPC失败事实不变。
3组空窗口的差异数为0，仅volume空列dtype不同，不误记为市场数值差异。

R2审计目录准备请求曾断连超时，之后先只读核对requested存在/result不存在，再上传
和启动唯一探针；没有重放未知送达的写操作。首次只读身份查询本机解码stderr异常，
修正为字节捕获后取得白名单结果；不回显SDK/私有配置异常消息。

## 15:29实际Windows候选验收

候选源码SHA256 `d5fb94b3c2fa4c824c557fad31835896135b9f1d0891d56d2c8fe7418eecc074`，
Python3.11.9实际执行PID3660（控制器启动的venv launcher PID4028），客户端5340保持。
5组非空/3组空窗口共2410行与本地源逐值一致，152根真实零量全部保留；实际Arrow
schema为string/timestamp[us]/double/int64，与既有MINUTE_BARS一致，Parquet回读完全一致。
下载/订阅/账户/交易/生产写均0，仅审计目录候选Parquet；不冒充完整下载main或自然全量验收。
[结果](qmt-local-minute-candidate-20261030.json)保留两个PID与实际源码/Parquet SHA。

候选全量4219 passed/7既有skipped/2既有依赖warnings。新增回归覆盖原生零量保留、null
落Arrow、重复/时间错配、分数成交量不截断及失败异常脱敏/不推进账本。普通日线量价仍由
原门禁检查，本轮未放宽阈值、未重写既有分钟分区。

## 修复范围与验收约束

对应[#550](https://github.com/wanlinruo/quant-trade/issues/550)、
[#478](https://github.com/wanlinruo/quant-trade/issues/478)，在#540数据主线内实施。
正式下载后只读明确的本地原生字段，显式关闭填充；空缓存报缺，不回退RPC或默认价。
保留本地返回的真实零量/缺失行；缺失业务值写null交既有质量门禁，不从形状推断并删行。
验证双重时间身份、重复索引、数值类型和整数成交量，不换单位、不截断、不放宽质量规则。

历史RPC过滤器仅可用于历史响应复放，不证明原生本地False数据需要同样删除；本轮不清理
历史分区、不改回测分钟源或冻结阈值，AGENTS.md中D-23约束保持。

生产下载器由daily-archive和Tushare晚到补采两者携带，Windows固定Gateway校验实际hash。
现有Gateway仅批准一个minute hash；候选只增加可选的一个精确previous_minute路径/hash，
客户端仍须显式请求已批准SHA，不是行情/API回退，也不自动重试。新旧脚本逐次核hash、
禁止写命名空间/reparse路径，先安装兼容入口再更新配置和两个消费者。正式升级仍在
两个生产者空闲且timer暂停的窗口联合编排，保留旧源码/配置与ACL。
minute导入器同键keep-last；即使只改读取API，也须实际比较原值差异并明确可能的重读窗口，
不能把新读取器安装成功等同于自然全量质量已通过。模拟/实盘恢复另守阶段门禁。
