import { api } from './client'
import type { ChunkItem, DocumentItem, Page } from '@/types'

export async function listDocuments(
  kbId: string,
  offset = 0,
  limit = 50,
): Promise<Page<DocumentItem>> {
  const { data } = await api.get<Page<DocumentItem>>(`/kbs/${kbId}/documents`, {
    params: { offset, limit },
  })
  return data
}

export async function uploadDocument(kbId: string, file: File): Promise<DocumentItem> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post<DocumentItem>(`/kbs/${kbId}/documents`, form, {
    timeout: 120_000,
  })
  return data
}

export async function getDocument(documentId: string): Promise<DocumentItem> {
  const { data } = await api.get<DocumentItem>(`/documents/${documentId}`)
  return data
}

export async function retryDocument(documentId: string): Promise<DocumentItem> {
  const { data } = await api.post<DocumentItem>(`/documents/${documentId}/retry`)
  return data
}

export async function deleteDocument(documentId: string): Promise<void> {
  await api.delete(`/documents/${documentId}`)
}

export async function listChunks(
  documentId: string,
  offset = 0,
  limit = 100,
): Promise<Page<ChunkItem>> {
  const { data } = await api.get<Page<ChunkItem>>(`/documents/${documentId}/chunks`, {
    params: { offset, limit },
  })
  return data
}
