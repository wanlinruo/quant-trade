import { getJson } from './client'
import type { Board, BoardSeries, BoardUnavailable } from './contracts.gen'

/** /api/app/board 的类型由后端契约生成（src/qsys/webapp/contracts/board.py）。 */
export type {
  Board, BoardCard, BoardColumn, BoardEvent, BoardMeta, BoardSentiment, BoardSeries, BoardShadow,
  BoardUnavailable, CompareGroup, CompareItem, ConceptCurve, EffectItem, LadderRow, LayerRow, MoneyEffect,
} from './contracts.gen'

export type BoardData = Board | BoardUnavailable
export type BoardMode = Board['mode']
export type CompareStatus = Board['compare'][number]['items'][number]['status']

export function fetchBoard(day: string | null, at: string | null, signal?: AbortSignal): Promise<BoardData> {
  const q = new URLSearchParams()
  if (day) q.set('day', day)
  if (day && at) q.set('at', `${day}T${at}`)
  const s = q.toString()
  return getJson<BoardData>(`/api/app/board${s ? `?${s}` : ''}`, signal)
}

export function fetchBoardSeries(day: string, symbol: string, signal?: AbortSignal): Promise<BoardSeries> {
  const q = new URLSearchParams({ day, symbol })
  return getJson<BoardSeries>(`/api/app/board/series?${q}`, signal)
}
