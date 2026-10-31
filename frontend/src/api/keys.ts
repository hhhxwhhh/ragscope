import { api } from './client'
import type { ApiKeyItem, AuditLogItem, Page } from '@/types'

export async function listApiKeys(): Promise<Page<ApiKeyItem>> {
  const { data } = await api.get<Page<ApiKeyItem>>('/api-keys', { params: { limit: 100 } })
  return data
}

export async function listScopes(): Promise<Record<string, string>> {
  const { data } = await api.get<Record<string, string>>('/api-keys/scopes')
  return data
}

export async function createApiKey(payload: {
  name: string
  scopes: string[]
  description?: string | null
  expires_in_days?: number | null
}): Promise<ApiKeyItem> {
  const { data } = await api.post<ApiKeyItem>('/api-keys', payload)
  return data
}

export async function revokeApiKey(keyId: string): Promise<void> {
  await api.delete(`/api-keys/${keyId}`)
}

export async function listKeyAudit(keyId: string): Promise<Page<AuditLogItem>> {
  const { data } = await api.get<Page<AuditLogItem>>(`/api-keys/${keyId}/audit`)
  return data
}
