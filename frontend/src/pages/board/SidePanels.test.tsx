import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { EChartsOption } from '../../page/EChart'
import { ConceptCurves, Timeline } from './SidePanels'
import { board } from './fixtures'

const options: EChartsOption[] = []
vi.mock('../../page/EChart', () => ({ EChart: ({ option }: { option: EChartsOption }) => { options.push(option); return <div data-testid="echart" /> } }))
afterEach(() => { options.length = 0; vi.useRealTimers() })

test('curves keep step shape and clip future', () => {
  vi.useFakeTimers({ toFake: ['Date'] }); vi.setSystemTime(new Date(2026, 10, 13, 9, 31, 5))
  render(<ConceptCurves data={board()} />)
  const s = (options[0].series as { name: string; step?: string; data: unknown[] }[])[0]
  expect(s.name).toBe('机器人')
  expect(s.step).toBe('end')
  expect(s.data).toEqual([['2026-11-13T09:30:58+08:00', 1]])
})

test('curve time axis labels use market time zone', () => {
  render(<ConceptCurves data={board()} />)
  const fmt = (options[0].xAxis as { axisLabel: { formatter: (v: number) => string } }).axisLabel.formatter
  expect(fmt(Date.parse('2026-11-13T01:30:00Z'))).toBe('09:30')
})

test('curves without concepts explain why', () => {
  render(<ConceptCurves data={board({ curves: [] })} />)
  expect(screen.getByText(/没有题材曲线/)).toBeInTheDocument()
})

test('timeline lists events and clicking a name opens the drawer', async () => {
  const onPick = vi.fn()
  render(<Timeline data={board()} onPick={onPick} />)
  expect(screen.getByText('首封 第1家')).toBeInTheDocument()
  expect(screen.getByText('开板')).toBeInTheDocument()
  expect(screen.getByText('回封')).toBeInTheDocument()
  expect(screen.getAllByText(/≈09:31:28/).length).toBe(1)
  await userEvent.click(screen.getAllByRole('button', { name: '甲科技' })[0])
  expect(onPick).toHaveBeenCalledWith('A.SH')
})
