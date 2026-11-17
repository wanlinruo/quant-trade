/** 6 组 19 页（设计文档 §4）；phase 为上线期次（§12）。legacy 为现有页面路径，新增页面为 null。 */
export interface NavPage { key: string; label: string; legacy: string | null; phase: 1 | 2 | 3 | 4; kw: string }
export interface NavGroup { group: string; pages: NavPage[] }

export const NAV: NavGroup[] = [
  { group: '总览', pages: [
    { key: 'today', label: '今日运行', legacy: '/', phase: 1, kw: '首页 总览' },
    { key: 'alerts', label: '告警中心', legacy: null, phase: 1, kw: '告警 通知' },
  ] },
  { group: '交易', pages: [
    { key: 'live', label: '实盘总览', legacy: '/live', phase: 1, kw: '持仓 资产 风险 额度' },
    { key: 'orders', label: '委托与成交', legacy: '/live', phase: 1, kw: '订单 委托 成交 拒单' },
    { key: 'recon', label: '对账与风控', legacy: '/live', phase: 1, kw: '对账 急停 停机闸 规则' },
    { key: 'runs', label: '运行记录', legacy: '/live', phase: 1, kw: '日报 运行 事件' },
    { key: 'paper', label: '模拟账户', legacy: '/paper', phase: 1, kw: '模拟 paper' },
  ] },
  { group: '行情', pages: [
    { key: 'market', label: '市场总览', legacy: '/market', phase: 3, kw: '大盘 情绪 指数' },
    { key: 'board', label: '打板看板', legacy: '/market/limit-up-board', phase: 3, kw: '涨停 连板 炸板 梯队' },
  ] },
  { group: '研究', pages: [
    { key: 'strategies', label: '策略', legacy: '/strategies', phase: 3, kw: '策略 源码 参数' },
    { key: 'backtest', label: '回测', legacy: '/backtests', phase: 3, kw: '回测 研究任务' },
    { key: 'ailu', label: 'AI 涨停', legacy: '/market/limit-up-shadow', phase: 3, kw: '影子 榜单 模型' },
  ] },
  { group: '数据', pages: [
    { key: 'dataov', label: '数据总览', legacy: '/data', phase: 4, kw: '血缘 健康' },
    { key: 'tables', label: '数据表', legacy: '/data/tables', phase: 4, kw: '表 字段 schema' },
    { key: 'catalog', label: '指标字典', legacy: '/data/catalog', phase: 4, kw: '指标 口径' },
    { key: 'quality', label: '数据质量', legacy: '/services', phase: 4, kw: '质检 规则' },
  ] },
  { group: '运维', pages: [
    { key: 'services', label: '服务看板', legacy: '/services', phase: 2, kw: '定时任务 服务' },
    { key: 'nodes', label: '节点与链路', legacy: null, phase: 2, kw: '节点 磁盘 CPU 隧道' },
    { key: 'releases', label: '发布与版本', legacy: null, phase: 2, kw: '发布 部署 版本' },
  ] },
]

export const PAGES: (NavPage & { group: string })[] = NAV.flatMap((g) => g.pages.map((p) => ({ ...p, group: g.group })))

export const DEFAULT_PAGE = 'today'

export function pageByKey(key: string) {
  return PAGES.find((p) => p.key === key)
}
