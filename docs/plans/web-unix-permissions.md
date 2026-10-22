# Web Unix权限收敛（#492 / #330）

原qsys-web账号的主组已为私有qsys-web，但还属于qsys-workbench；实际unit则把共享组设为
主组。unit沙箱阻断结果目录写入，不能替代Unix账号最小权限。此次仅收敛Web身份，业务入口、
状态位置、策略/任务协议与Workbench owner不变。

## 已确认的边界

10-22投影身份UID990/GID982、仅observers组读取真实生产路径：runs共47目录/769文件，
没有任何Web所有的inode、没有任何可写项，遍历无错误。证明去掉workbench组即可消除额外
结果写权限，无须转移历史所有权。该探针不是生产身份已切换。

Workbench继续owner=qsys-web、group=qsys-workbench，保留worker与Paper合法写者、原命名ACL
及历史。仅对Workbench现有文件/目录补u:qsys-web:rwX，目录default ACL补u:qsys-web:rwx，
确保未来由worker创建的SQLite WAL/SHM或策略文件仍可被Web访问；不把runs加入写集合。
unit改Group=qsys-web，重置SupplementaryGroups后仅列qsys-observers。

## 准备与发布

1. 正常test CI/测试环境/PR自审/合并。保存现场账号groups、实际unit/PID、Workbench与runs
   完整getfacl备份、当前配置指纹及一致性SQLite备份，审计根`issue492-<日期>`。
2. 检查目标及父目录类型，无symlink逃逸。只在Workbench补named Web access/default ACL，
   不递归chown、不改worker/Paper身份、不变更runs、配置或队列记录。
3. 在隔离一致性副本中，用实际当前Web制品和投影私有身份验证创建/修改草稿、SQLite/WAL、
   入队与worker领取；隔离副本不被生产worker扫描。验证实际Windows只读key、Live状态、
   服务历史/结果可读且不可写，原事实与合法worker写权限保留。
4. 去掉qsys-web账号唯一多余的qsys-workbench补充组，保留私有主组与observers；核对当前
   运行Web PID及内核groups尚未变化。随后以merged版本正常引导可信manifest，标准Web
   dry/formal发布切换unit，等待workflow/Deployment/实际validator三方成功。
5. 新进程/proc身份、实际Unix账号和实际unit都核对；Workbench可写，runs全部inode只读，
   完整配置/Live/Paper/Shadow/其他组件状态写入拒绝，Windows只读入口与关键页面通过。
   Live与worker PID/启动时间不变，不在生产提交测试策略或回测任务。

异常保留证据，按#453修复正常发布；安装器自带失败补偿保留，不能以删除数据、放宽配置或
给回共享组掩盖失败。准备ACL不改变原生产者的业务数据，未执行项必须如实交接。
