import { parseLocal, phaseOf } from './time'

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
