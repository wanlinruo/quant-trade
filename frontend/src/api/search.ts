import { getJson } from './client'
import type { SearchIndex } from './contracts.gen'

export type { SearchIndex, SearchItem } from './contracts.gen'

export const fetchSearchIndex = (signal?: AbortSignal) => getJson<SearchIndex>('/api/app/search', signal)
