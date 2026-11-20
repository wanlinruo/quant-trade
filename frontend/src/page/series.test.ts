import { clipFuture, toSeries } from './series'

test('toSeries keeps nulls and order', () => {
  const rows = [{ d: '11-13', v: 1 }, { d: '11-14', v: null }, { d: '11-17', v: undefined }, { d: '11-18', v: NaN }]
  expect(toSeries(rows, (r) => r.d, (r) => r.v as number | null)).toEqual([['11-13', 1], ['11-14', null], ['11-17', null], ['11-18', null]])
})

test('clipFuture drops points after now', () => {
  const pts: [string, number | null][] = [['2026-11-17', 1], ['2026-11-18', 2]]
  expect(clipFuture(pts, new Date(2026, 10, 17, 20), (x) => new Date(x + 'T15:00:00'))).toEqual([['2026-11-17', 1]])
})
