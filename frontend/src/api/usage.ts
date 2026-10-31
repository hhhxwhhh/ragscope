import { api } from './client'
import type { UsageSummary } from '@/types'

export async function getUsageSummary(days = 14): Promise<UsageSummary> {
  const { data } = await api.get<UsageSummary>('/usage/summary', { params: { days } })
  return data
}
