import { int, pct, signedPct, yi } from './format'

test('formatters return null for null and keep sign', () => {
  expect(pct(null)).toBeNull(); expect(pct(0.623)).toBe('62.3%')
  expect(signedPct(null)).toBeNull(); expect(yi(null)).toBeNull(); expect(int(null)).toBeNull()
  expect(signedPct(0.0123)).toBe('+1.23%'); expect(signedPct(-0.005)).toBe('-0.50%')
  expect(yi(123_400_000_000)).toBe('1234 亿'); expect(int(5441)).toBe('5,441')
  expect(pct(0)).toBe('0.0%'); expect(int(0)).toBe('0')
})
