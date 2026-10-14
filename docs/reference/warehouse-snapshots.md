# Warehouse 不可变快照协议

本协议是 #285 的第一阶段交付。它定义研究与交易消费者如何引用一个可复现的数据版本，
不包含 Research Runner、生产账号或 cgroup 上线。

## 目录布局

```text
<snapshot-store>/
├── current -> snapshots/<snapshot_id>
└── snapshots/
    └── <snapshot_id>/
        ├── manifest.json
        └── warehouse/
            └── ...
```

`current` 通过同目录临时符号链接和 `os.replace` 原子切换。作业开始时必须解析并记录确定的
`snapshot_id`；运行过程中不得反复跟随 `current`。

## Manifest

`manifest.json` 的格式为 `qsys-warehouse-snapshot`、版本为 `1`，包含：

- `snapshot_id`、UTC `created_at`、`complete_through`；
- 数据 `schema_version` 与可选的发布代码 `source_revision`；
- 全部分区的相对路径、字节数和 SHA-256；
- Parquet 分区的行数与 Arrow schema SHA-256；
- 按规范 JSON 计算的分区集合 `content_sha256`。

路径不记录源机器绝对路径，因此 manifest 可以随快照迁移。验证会拒绝缺失文件、额外文件、
符号链接、路径逃逸、字节或 schema 漂移。

## 发布语义

当前实现使用同文件系统硬链接生成快照，原因是生产数据盘空间有限，而 Warehouse 的正式
写入协议采用“写新 inode，再原子替换目标路径”。因此后续正常发布不会修改旧快照引用的
inode。发布仍必须满足以下条件：

1. 全程持有生产 `warehouse-write.lock`；
2. Warehouse 与 snapshot store 位于同一文件系统；
3. 所有正式 Warehouse 写入继续使用原子替换，禁止原地改写；
4. Snapshot Publisher 是快照目录唯一写入者；消费者通过只读挂载访问；
5. 发布后执行完整 hash 验证，验证成功后才允许移动 `current`。

若特权进程违反第 3 条，快照验证会发现 hash 漂移，但硬链接不能在物理层阻止它。生产接入
前还需完成写入路径审计和独立 `qsys-research` 只读挂载负向测试。

## 命令

调用者负责使用与数据任务相同的写锁：

```bash
flock --exclusive var/warehouse-write.lock \
  .venv/bin/python scripts/warehouse_snapshot.py create \
  --warehouse /data/qsys/warehouse \
  --store /data/qsys/warehouse-snapshot-store \
  --complete-through 2026-10-13 \
  --schema-version qsys-data-v1 \
  --source-revision <完整提交SHA> \
  --publish-current

.venv/bin/python scripts/warehouse_snapshot.py verify \
  --snapshot /data/qsys/warehouse-snapshot-store/snapshots/<snapshot_id>

.venv/bin/python scripts/warehouse_snapshot.py show-current \
  --store /data/qsys/warehouse-snapshot-store
```

当前阶段不会自动安装 timer，也不会读取或修改生产 Warehouse。正式路径、发布频率、保留
数量和挂载方式将在 Runner 与资源隔离阶段一起通过测试环境验证。
