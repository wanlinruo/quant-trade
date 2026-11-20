import { dfx } from '../data-fixtures'
import { domainLabel, lineageText, sourceText } from './labels'

test('labels cover every domain code in the catalog', () => {
  for (const t of dfx.overview().tables) {
    const label = domainLabel(t.domain)
    expect(label).not.toBe(t.domain.code)
    expect(label.length).toBeGreaterThan(0)
  }
})

test('unclassified domain shows reason', () => {
  expect(domainLabel({ code: 'unclassified', unclassified_reason: '临时研究表' })).toBe('未归类：临时研究表')
  expect(domainLabel({ code: 'unclassified', unclassified_reason: null })).toBe('未归类')
})

test('source and lineage text follow old wording', () => {
  expect(sourceText({ kind: 'source', id: 'x', provider: 'Tushare', api: 'daily', status: 'active' })).toBe('Tushare·daily')
  expect(sourceText({ kind: 'table', id: 'bars_daily', provider: null, api: null, status: null })).toBe('表：bars_daily')
  const row = dfx.catalog().rows[0]
  expect(lineageText({ ...row, direct: { source_id: 's', provider: 'Tushare', api: 'daily', field: 'vol', multiplier: 100 } })).toBe('Tushare·daily.vol ×100')
  expect(lineageText({ ...row, direct: null, roots: [] })).toBe('（无法追溯到直取源）')
})
