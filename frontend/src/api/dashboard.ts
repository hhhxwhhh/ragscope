import { api } from './client'
import type { DashboardSummary, DashboardTrend } from '@/types'

export async function getSummary(): Promise<DashboardSummary> {
  const { data } = await api.get<DashboardSummary>('/dashboard/summary')
  return data
}

export async function getTrend(days = 14): Promise<DashboardTrend> {
  const { data } = await api.get<DashboardTrend>('/dashboard/trend', { params: { days } })
  return data
}
