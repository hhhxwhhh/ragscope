import { api } from './client'
import type { KnowledgeBase, KnowledgeBaseCreate, Page } from '@/types'

export async function listKbs(offset = 0, limit = 20): Promise<Page<KnowledgeBase>> {
  const { data } = await api.get<Page<KnowledgeBase>>('/kbs', { params: { offset, limit } })
  return data
}

export async function createKb(payload: KnowledgeBaseCreate): Promise<KnowledgeBase> {
  const { data } = await api.post<KnowledgeBase>('/kbs', payload)
  return data
}

export async function getKb(kbId: string): Promise<KnowledgeBase> {
  const { data } = await api.get<KnowledgeBase>(`/kbs/${kbId}`)
  return data
}

export async function deleteKb(kbId: string): Promise<void> {
  await api.delete(`/kbs/${kbId}`)
}
