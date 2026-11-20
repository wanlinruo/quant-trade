import { NAV, PAGES, pageByKey } from './nav'

test('NAV has 6 groups and 19 unique pages, each with legacy path and phase', () => {
  expect(NAV.map((g) => g.group)).toEqual(['总览', '交易', '行情', '研究', '数据', '运维'])
  const pages = NAV.flatMap((g) => g.pages)
  expect(pages).toHaveLength(19)
  expect(new Set(pages.map((p) => p.key)).size).toBe(19)
  for (const p of pages) {
    if (p.legacy !== null) expect(p.legacy).toMatch(/^\//)
    expect([1, 2, 3, 4]).toContain(p.phase)
  }
})

test('only the three brand-new pages have no legacy page', () => {
  expect(PAGES.filter((p) => p.legacy === null).map((p) => p.key).sort()).toEqual(['alerts', 'nodes', 'releases'])
})

test('phases follow the design: overview/trading 1, ops 2, market/research 3, data 4', () => {
  const phaseOf = (g: string) => new Set(NAV.find((x) => x.group === g)!.pages.map((p) => p.phase))
  expect(phaseOf('总览')).toEqual(new Set([1]))
  expect(phaseOf('交易')).toEqual(new Set([1]))
  expect(phaseOf('运维')).toEqual(new Set([2]))
  expect(phaseOf('行情')).toEqual(new Set([3]))
  expect(phaseOf('研究')).toEqual(new Set([3]))
  expect(phaseOf('数据')).toEqual(new Set([4]))
})

test('pageByKey falls back to undefined for unknown keys', () => {
  expect(pageByKey('orders')?.label).toBe('委托与成交')
  expect(pageByKey('nope')).toBeUndefined()
})
