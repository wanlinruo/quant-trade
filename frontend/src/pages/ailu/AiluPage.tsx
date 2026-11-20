import { Alert, Tabs } from 'antd'
import { setSearch, useSearch } from '../../shell/router'
import { ResearchView } from './ResearchView'
import { ShadowView } from './ShadowView'

export const NOTICE = '不是交易信号，不进入实盘或模拟决策。'
const DAY = /^\d{4}-\d{2}-\d{2}$/

/** AI 涨停（§8.12）：影子榜单与研究回测，子标签与日期写进 URL。 */
export function AiluPage() {
  const q = useSearch()
  const tab = q.get('tab') === 'research' ? 'research' : 'shadow'
  const raw = q.get('day')
  const day = raw && DAY.test(raw) ? raw : null
  const pick = (d: string | null) => setSearch({ day: d })
  return (
    <>
      <Alert type="info" showIcon title={NOTICE} style={{ marginBottom: 8 }} />
      <Tabs activeKey={tab} onChange={(k) => setSearch({ tab: k === 'shadow' ? null : k, day: null })}
        items={[
          { key: 'shadow', label: '影子榜单', children: tab === 'shadow' ? <ShadowView key={day ?? 'latest'} day={day} onPickDay={pick} /> : null },
          { key: 'research', label: '研究回测', children: tab === 'research' ? <ResearchView day={day} onPickDay={pick} /> : null },
        ]} />
    </>
  )
}
