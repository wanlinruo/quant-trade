import { getJson } from './client'
import type { ResearchBoard, ShadowBoard } from './contracts.gen'

export type {
  ResearchBoard, ResearchDay, ResearchEntry, ResearchHistoryDay, ResearchMonth, ResearchSummary,
  ShadowBoard, ShadowDay, ShadowEntry, ShadowSnapshot,
} from './contracts.gen'
export type ViewState = ShadowBoard['state']

const q = (day?: string | null) => (day ? `?day=${encodeURIComponent(day)}` : '')

export const fetchShadow = (day?: string | null, signal?: AbortSignal) =>
  getJson<ShadowBoard>(`/api/app/ailu/shadow${q(day)}`, signal)

export const fetchResearch = (day?: string | null, signal?: AbortSignal) =>
  getJson<ResearchBoard>(`/api/app/ailu/research${q(day)}`, signal)
