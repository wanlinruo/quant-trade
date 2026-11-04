# 2026-11-04 组件旧制品清理审批方案（#126）

**状态：待维护者明确确认，尚未删除。** 维护者“继续推进”仅用于准备此方案及独立代码工作，
不作为生产目录删除许可。本方案只覆盖下面21个明确目录，不扩展到数据盘留存资产或新发现项。

依据AGENTS.md §4、§2.1以及[存储规则](../reference/storage-retention.md)。

## 只读核验结果

- 盘点59个受节点组件清单管理的release，保留38个、候选21个；扫描错误0。
- 保留所有16个current、13个最近成功发布的补偿目标，以及实际进程的exe/cwd/fd/maps、
  已加载/已安装unit、cron、外部软链接与挂载引用；同一版本可能有多项保护理由。
- 每个组件最近成功生产record均调用实际`validate_record()`，其after与当前指针一致。
  旧JSON审计的历史引用不单独阻止清理，但仍被真实unit/软链接使用的旧版本继续保留。
- 按候选目录跨路径的(dev,inode)并集扣除外部硬链接后估算释放18254508032 bytes
  （17.000835 GiB），不把目录表观量简单相加。
- 核验期间12个业务/数据peer身份和全部current指针保持，未停timer、未启动业务或读取市场事实。
- 只读临时unit实测UID0/PID1221075，Invocation `cd85d213e3204c26b7b82da3f0c5b1ea`；
  CPU25%/Memory1536MiB/IO idle/Nice15，生产路径只读，仅允许写审计目录；成功终态后已停止。

节点永久审计：`/data/qsys/var-deployments/issue126-release-plan-20261104-r1/`，包含
requested/actual-running/terminal/journal、inventory、release-ledger、cleanup-manifest与scope。

## 容量预算

盘点时根盘可用11074662400 bytes（约10.31GiB），数据盘17034395648 bytes（约15.86GiB）。
若21项目全部按审批清理，根盘静态预计约27.31GiB；预留两份Linux候选安装各1GiB的暂估
预算后约25.31GiB。最终预算仍以实际制品、依赖与暂存开销重新核对，不能当成固定安装上限。
数据盘空间不会因本次清理增加，仍保留其warning；Windows空间和组件维护门禁另行核验。

## 精确清单

完整路径、release manifest SHA及目录元数据SHA见
[机器清单](component-release-cleanup-20261104.json)。以下版本号与对应组件的规范release root
拼接成完整路径；catalog-verify位于`/opt/qsys-data/releases/catalog-verify/`，其余位于
`/opt/qsys/components/<组件>/releases/`。

机器清单SHA256：`55294c7694c5a6e262786081accfaf42db06ab64d88e9f0fad0994491b4ddf3f`。

| 组件 | 精确版本 | 目录去重占用GiB（非相加释放量） |
|---|---|---:|
| web | `1dac3edb73b335911af253cd9310d47290b89dc8` | 0.810 |
| web | `9727d803989ac0e9bff344298de84d260842e08f` | 0.810 |
| web | `b215c39b42df0758562c51e25aaadd86ee432eff` | 0.810 |
| catalog-verify | `3e40c218d266ed00706952fef4c66711412119ef` | 0.813 |
| catalog-verify | `80f7068d622c3109cef59bf382cedb629a639116` | 0.813 |
| catalog-verify | `8d8307e9b2c6cb363facd9beb54dbf7a18d7fa4d` | 0.808 |
| catalog-verify | `b215c39b42df0758562c51e25aaadd86ee432eff` | 0.809 |
| service-checks | `0a67a683b6f58527ae220ba54d6ce684af5c6550` | 0.809 |
| service-checks | `1dac3edb73b335911af253cd9310d47290b89dc8` | 0.809 |
| service-checks | `7d5e2c41c0eca38c4d8ac3f2815c8674c028a6e0` | 0.809 |
| service-checks | `a6bc982f927c542b049e885019eb99e0fd615aa0` | 0.809 |
| service-checks | `eb6e9186d1038afea3beb5f71f0f7cb6c299ac39` | 0.809 |
| daily-archive | `b760b5f91eb0ff00e8737ed90e627c2a1f4557da` | 0.809 |
| tushare-sync | `2d2d25ba6d24b1eab9c2599b489daa4d07950bb8` | 0.809 |
| tushare-sync | `a132291534295e853cd0a7815829d0ffa4f88f17` | 0.809 |
| tushare-sync | `b760b5f91eb0ff00e8737ed90e627c2a1f4557da` | 0.809 |
| tushare-sync | `d50592a3b75cfe81458b90773793c8aeea8f97a2` | 0.809 |
| tushare-sync | `f09dcf785c9ced7b4bb94ff3afab88b147b9de32` | 0.809 |
| health-observer | `1dac3edb73b335911af253cd9310d47290b89dc8` | 0.809 |
| health-observer | `a6bc982f927c542b049e885019eb99e0fd615aa0` | 0.809 |
| health-observer | `eb6e9186d1038afea3beb5f71f0f7cb6c299ac39` | 0.809 |

## 执行条件

1. 取得维护者对本清单21目录的明确确认，并把确认文本、清单SHA和精确范围留入节点审计。
2. 执行前重新核对current、最近成功补偿、进程/maps、unit/cron/软链接及候选目录身份；
   重新绑定manifest与元数据指纹。出现新增引用/内容漂移时保留该项，不扩大范围。
3. 仅处理获批精确目录，不跟随符号链接；不删生产数据、部署审计、业务状态、私有配置或
   /data/qsys留存资产，不做交易激活或故障注入。
4. 按实际df记录释放量，并核对所有保护版本/peer连续性；失败记录保留，不用reset-failed掩盖。
   发布与实际任务验收仍分开执行；清理成功不会自动解除任何Live/Shadow或Paper维护门禁。

此方案不包含自动删除脚本或定时清理规则；审批前只读准备和本地集成可继续。
