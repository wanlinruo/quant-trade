# 数据组件 Windows 限权取数（#476 / #475 / #330）

状态：实现与本地验证；尚未创建组件 key 或改 Windows 控制面。实际生产状态以 Issue 最新记录为准。

## 固定协议

Linux 使用独立组件 key、显式 known_hosts、StrictHostKeyChecking=yes、IdentitiesOnly=yes，
不读取用户 SSH 配置。Windows authorized_keys 的 forced command 固定组件身份、Python、
已合并 main 的入口与 root/Administrators 控制配置。只允许 `qsys-data <base64url-json>`，
拒绝 shell、SFTP、SCP、未知字段、跨组件路径、符号链接/junction、任意脚本与文件名。

协议版本1：probe、prepare、put清单、run已批准下载器、list产物、get产物。
每组件独立工作根；作业名仅 minute_YYYYMMDD / auction_YYYYMMDD。
清单仅 symbol,dt、合法六位证券代码及SH/SZ/BJ、真实日期；最大32MiB/20万股日，
日期必须落在最多31天的批准窗口内；长度/hash完整核验后原子发布。
执行器只能选择控制配置中只读分钟/竞价下载器，分别核对调用方、配置和实际文件SHA256；
Python以-I -B -X utf8运行，参数数组无shell。分钟原进度协议、run_id和退出码2保持原语义。

回传仅Parquet、progress/result JSON与done/failed CSV，单文件最大256MiB；
清单含长度/hash，Linux先校验临时文件再发布。已有不同Parquet拒绝覆盖；可变进度元数据保留
旧字节备份再更新。归档仍在成功取回、结果身份/未知空洞/导入/质量门禁全部通过后发布canonical。

## 接入与兼容

- daily-archive 新增可选 --win-transport gateway、--win-component、--win-known-hosts，
  分钟/竞价分别从本组件hash-bound source选择下载器。默认legacy保留现有共享任务行为。
- tushare-sync 的有限晚到分钟补采将 transport/known_hosts/自身组件身份传给包内归档入口，
  不关闭补采；子进程禁写字节码。
- 两组件制品明确包含分钟与竞价下载器；Windows入口从相同成功CI的已合并源码引导安装，
  不接受组件自己上传任意可执行代码。新增下载器hash须经明确的控制面准备；源脚本未变时复用批准副本。
- Gateway配置不含交易账户、Tushare token或私钥；私钥只在各Linux专属目录，Windows仅追加
  from=Linux节点IP/restrict/forced-command公钥条目，旧个人条目、密码登录和原文件ACL保留。

## 实施与验收顺序

1. 专项/全量测试与逐文件自审；可信main与候选planner检查所有新增路径。
   deploy/windows/**已属于Windows控制面及测试runtime，不能标成忽略路径。
2. test CI→测试部署→test/main PR与代行自审→合并对齐→最终SHA CI。
   仅最终SHA源码与实际需要制品；不重复下载/测试未变的中间版本。
3. 盘点Windows目标是文件/目录/reparse point，保存原authorized_keys hash/ACL/字节备份、
   QMT/交易/行情任务与PID、Linux关键peer；准备独立账号/key/最小配置/已信任host key。
4. 固定入口、批准下载器与配置先在独立目录安装并校验hash/权限，再原子追加authorized_keys，
   不改旧条目、不重启sshd/QMT/交易/行情任务。备份与配置记录留在节点审计目录。
5. 真实专属key probe、跨namespace/任意命令/脚本hash/清单/路径逃逸拒绝；真实已收盘日少量
   QMT取数及传输完整性对账，只写隔离工作区，不覆盖生产Warehouse。
6. 之后由#475与daily-archive迁移子Issue分别启用gateway路径、验证真实生产账号和Linux写表集合，
   正式workflow/Deployment/实际validate_record三方成功后做业务与自然调度验收。

2026-10-21只读Windows探针：现有 C:/qsys/.venv/Scripts/python.exe 为3.11.9，
在-I -B下可导入xtquant与pyarrow；不据此提前声称新key/网关或真实下载已验收。

## 认证前连接池限流（#508）

10-22 17:04:52 Windows OpenSSH实际记录MaxStartups丢弃生产Linux节点连接，导致#475晚到
分钟补采失败；公网无效登录连接与该时段限流同时存在。脱敏证据位于issue475审计根
`windows-ssh-maxstartups-20261022.json`。服务端隔离/抗扫描由#122继续承接，保留密码登录。

Gateway JSON、文件取回和分钟下载流仅对rc255且输出严格符合认证前kex reset签名的情况，
最多尝试3次、退避2/5秒，共用原超时预算。出现业务输出、文件部分字节、认证/会话错误、
业务失败或超时均不自动重发；已有Parquet/hash/身份/质量门禁保持。重试有明确日志，不隐藏
首次连接失败。该代码修复的正式发布/自然取数验收以#508/#475最新记录为准。
