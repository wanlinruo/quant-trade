import { getJson } from './client'
import type { NodesView, OperationsDisabled, ReleasesView, ServicesView } from './contracts.gen'

export type { ComponentRelease, LinkInfo, NodeInfo, NodesView, OperationsDisabled, ProcessInfo,
  ReleaseEvent, ReleasesView, SecurityCheck, ServiceRow, ServiceRunItem, ServicesView } from './contracts.gen'

const day = (d: string | null) => d ? `?day=${encodeURIComponent(d)}` : ''
export const fetchServices = (d: string | null, s?: AbortSignal) => getJson<ServicesView | OperationsDisabled>(`/api/app/operations/services${day(d)}`, s)
export const fetchNodes = (s?: AbortSignal) => getJson<NodesView | OperationsDisabled>('/api/app/operations/nodes', s)
export const fetchReleases = (s?: AbortSignal) => getJson<ReleasesView | OperationsDisabled>('/api/app/operations/releases', s)
