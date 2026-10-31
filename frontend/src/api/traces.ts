import { api } from './client'
import type { Page, TraceDetail, TraceSummary } from '@/types'

export async function listTraces(
  kbId: string,
  params: { limit?: number; offset?: number } = {},
): Promise<Page<TraceSummary>> {
  const { data } = await api.get<Page<TraceSummary>>(`/kbs/${kbId}/traces`, { params })
  return data
}

export async function getTrace(traceId: string): Promise<TraceDetail> {
  const { data } = await api.get<TraceDetail>(`/traces/${traceId}`)
  return data
}
