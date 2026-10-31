import { api } from './client'
import type { CorpusImportStatus, CorpusPack, CorpusPackDetail } from '@/types'

export async function listCorpusPacks(): Promise<CorpusPack[]> {
  const { data } = await api.get<CorpusPack[]>('/corpus')
  return data
}

export async function getCorpusPack(slug: string): Promise<CorpusPackDetail> {
  const { data } = await api.get<CorpusPackDetail>(`/corpus/${slug}`)
  return data
}

export async function readCorpusDoc(slug: string, file: string): Promise<string> {
  const { data } = await api.get<{ content: string }>(`/corpus/${slug}/docs/${encodeURIComponent(file)}`)
  return data.content
}

export async function importCorpus(slug: string, kbId: string): Promise<CorpusImportStatus> {
  const { data } = await api.post<CorpusImportStatus>(`/corpus/${slug}/import`, { kb_id: kbId })
  return data
}

export async function getImportStatus(slug: string, kbId: string): Promise<CorpusImportStatus> {
  const { data } = await api.get<CorpusImportStatus>(`/corpus/${slug}/import/${kbId}`)
  return data
}
