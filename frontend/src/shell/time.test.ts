import { dayLabel, hms, parseLocal, phaseOf } from './time'

const at = (dow: number, h: number, m: number) => new Date(2026, 10, 15 + dow, h, m) // 2026-11-15 是周日

test('phaseOf follows A-share session times on weekdays', () => {
  expect(phaseOf(at(1, 9, 0))).toBe('盘前')
  expect(phaseOf(at(1, 9, 20))).toBe('集合竞价')
  expect(phaseOf(at(1, 10, 0))).toBe('盘中')
  expect(phaseOf(at(1, 12, 0))).toBe('午间休市')
  expect(phaseOf(at(1, 14, 59))).toBe('盘中')
  expect(phaseOf(at(1, 15, 0))).toBe('盘后')
})

test('phaseOf never claims trading on weekends', () => {
  expect(phaseOf(at(0, 10, 0))).toBe('周末休市')
  expect(phaseOf(at(6, 10, 0))).toBe('周末休市')
})

test('parseLocal returns null for missing or malformed values', () => {
  expect(parseLocal(null)).toBeNull()
  expect(parseLocal('not a date')).toBeNull()
  expect(parseLocal('2026-11-17T10:00:00')?.getHours()).toBe(10)
})

test('times are shown in Asia/Shanghai regardless of browser timezone', () => {
  // 18:00 UTC = 次日 02:00 上海；不论运行环境时区，都按市场时区显示
  const d = new Date('2026-11-18T18:00:00Z')
  expect(hms(d)).toBe('02:00:00')
  expect(dayLabel(d)).toBe('2026-11-19 周四')
  expect(phaseOf(new Date('2026-11-19T02:00:00Z'))).toBe('盘中') // 上海 10:00 周四
})

test('dateTime formats in market time zone and reports unknown', async () => {
  const { dateTime } = await import('./time')
  expect(dateTime('2026-11-18T03:04:05Z')).toBe('2026-11-18 11:04:05')
  expect(dateTime('2026-11-18T11:04:05+08:00')).toBe('2026-11-18 11:04:05')
  expect(dateTime(null)).toBe('未知')
  expect(dateTime('bad')).toBe('未知')
})
