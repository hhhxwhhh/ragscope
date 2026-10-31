import { api } from './client'
import type { OpsSnapshot } from '@/types'

export async function getOpsSummary(): Promise<OpsSnapshot> {
  const { data } = await api.get<OpsSnapshot>('/ops/summary')
  return data
}

/** The Prometheus scrape endpoint lives outside the API prefix. */
export const METRICS_URL = '/metrics'
