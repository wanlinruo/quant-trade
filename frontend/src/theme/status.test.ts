import { palette, type Mode } from './palette'
import { statusColor, statusSymbol, statusText, type Status } from './status'

const ALL: Status[] = ['ok', 'warn', 'bad', 'idle', 'running', 'closed']
const MODES: Mode[] = ['dark', 'light']

function hexRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '')
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number]
}

test('every status has symbol, text and a distinct color in both modes', () => {
  for (const s of ALL) {
    expect(statusSymbol[s]).toBeTruthy()
    expect(statusText[s]).toBeTruthy()
  }
  for (const m of MODES) {
    const p = palette[m]
    expect(new Set((['ok', 'warn', 'bad'] as Status[]).map((s) => statusColor(p, s))).size).toBe(3)
    expect(p.up).not.toBe(p.down)
  }
})

test('status symbols and texts match the design spec', () => {
  expect(statusSymbol).toEqual({ ok: '●', warn: '▲', bad: '✕', idle: '○', running: '◐', closed: '—' })
  expect(statusText).toEqual({ ok: '正常', warn: '警告', bad: '失败', idle: '待运行', running: '运行中', closed: '休市' })
})

test('A-share convention: up is red-ish, down is green-ish', () => {
  for (const m of MODES) {
    const [r1, g1] = hexRgb(palette[m].up)
    const [r2, g2] = hexRgb(palette[m].down)
    expect(r1).toBeGreaterThan(g1)
    expect(g2).toBeGreaterThan(r2)
  }
})
