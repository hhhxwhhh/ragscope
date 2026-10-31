import { api } from './client'
import type { GraphPayload } from '@/types'

export async function getGraph(kbId: string): Promise<GraphPayload> {
  const { data } = await api.get<GraphPayload>(`/kbs/${kbId}/graph`)
  return data
}

export async function buildGraph(kbId: string): Promise<{ status: string }> {
  const { data } = await api.post<{ status: string }>(`/kbs/${kbId}/graph/build`)
  return data
}

export async function clearGraph(kbId: string): Promise<void> {
  await api.delete(`/kbs/${kbId}/graph`)
}
