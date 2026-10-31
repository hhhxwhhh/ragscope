import { api } from './client'
import type { DataSourceItem, SourcePreview, SyncRunItem } from '@/types'

export async function listSources(kbId: string): Promise<DataSourceItem[]> {
  const { data } = await api.get<DataSourceItem[]>(`/kbs/${kbId}/sources`)
  return data
}

export async function createSource(kbId: string, payload: Record<string, unknown>): Promise<DataSourceItem> {
  const { data } = await api.post<DataSourceItem>(`/kbs/${kbId}/sources`, payload)
  return data
}

export async function previewSource(kbId: string, payload: Record<string, unknown>): Promise<SourcePreview> {
  const { data } = await api.post<SourcePreview>(`/kbs/${kbId}/sources/preview`, payload, {
    timeout: 60_000,
  })
  return data
}

export async function updateSource(sourceId: string, payload: Record<string, unknown>): Promise<DataSourceItem> {
  const { data } = await api.patch<DataSourceItem>(`/sources/${sourceId}`, payload)
  return data
}

export async function deleteSource(sourceId: string): Promise<void> {
  await api.delete(`/sources/${sourceId}`)
}

export async function syncSource(sourceId: string): Promise<SyncRunItem> {
  const { data } = await api.post<SyncRunItem>(`/sources/${sourceId}/sync`)
  return data
}

export async function listSourceRuns(sourceId: string): Promise<SyncRunItem[]> {
  const { data } = await api.get<SyncRunItem[]>(`/sources/${sourceId}/runs`)
  return data
}
