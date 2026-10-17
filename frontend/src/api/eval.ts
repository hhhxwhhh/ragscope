import { api } from './client'
import type {
  ChunkSearchItem,
  EvalCase,
  EvalCompare,
  EvalDataset,
  EvalRun,
  EvalRunDetail,
  Page,
  RelevantRef,
} from '@/types'

export interface EvalCasePayload {
  question: string
  relevant_refs: RelevantRef[]
  answerable: boolean
  notes?: string | null
}

export interface RetrievalOverrides {
  vector_top_k?: number
  bm25_top_k?: number
  fusion_top_k?: number
  final_top_k?: number
  rrf_k?: number
  vector_weight?: number
  bm25_weight?: number
  min_vector_score?: number
  use_rerank?: boolean
  rerank_timeout_seconds?: number
}

export interface EvalRunPayload {
  config?: RetrievalOverrides
  base_run_id?: string | null
}

export async function listDatasets(
  kbId: string,
  offset = 0,
  limit = 50,
): Promise<Page<EvalDataset>> {
  const { data } = await api.get<Page<EvalDataset>>(`/kbs/${kbId}/eval/datasets`, {
    params: { offset, limit },
  })
  return data
}

export async function createDataset(
  kbId: string,
  payload: { name: string; description?: string | null },
): Promise<EvalDataset> {
  const { data } = await api.post<EvalDataset>(`/kbs/${kbId}/eval/datasets`, payload)
  return data
}

export async function deleteDataset(datasetId: string): Promise<void> {
  await api.delete(`/eval/datasets/${datasetId}`)
}

export async function listCases(
  datasetId: string,
  offset = 0,
  limit = 100,
): Promise<Page<EvalCase>> {
  const { data } = await api.get<Page<EvalCase>>(`/eval/datasets/${datasetId}/cases`, {
    params: { offset, limit },
  })
  return data
}

export async function createCase(
  datasetId: string,
  payload: EvalCasePayload,
): Promise<EvalCase> {
  const { data } = await api.post<EvalCase>(`/eval/datasets/${datasetId}/cases`, payload)
  return data
}

export async function bulkCreateCases(
  datasetId: string,
  cases: EvalCasePayload[],
): Promise<EvalCase[]> {
  const { data } = await api.post<EvalCase[]>(`/eval/datasets/${datasetId}/cases/bulk`, {
    cases,
  })
  return data
}

export async function deleteCase(caseId: string): Promise<void> {
  await api.delete(`/eval/cases/${caseId}`)
}

export async function listRuns(
  datasetId: string,
  offset = 0,
  limit = 50,
): Promise<Page<EvalRun>> {
  const { data } = await api.get<Page<EvalRun>>(`/eval/datasets/${datasetId}/runs`, {
    params: { offset, limit },
  })
  return data
}

export async function createRun(
  datasetId: string,
  payload: EvalRunPayload,
): Promise<EvalRun> {
  const { data } = await api.post<EvalRun>(`/eval/datasets/${datasetId}/runs`, payload)
  return data
}

export async function getRun(runId: string): Promise<EvalRunDetail> {
  const { data } = await api.get<EvalRunDetail>(`/eval/runs/${runId}`)
  return data
}

export async function cancelRun(runId: string): Promise<EvalRun> {
  const { data } = await api.post<EvalRun>(`/eval/runs/${runId}/cancel`)
  return data
}

export async function compareRuns(
  baseRunId: string,
  candidateRunId: string,
): Promise<EvalCompare> {
  const { data } = await api.get<EvalCompare>('/eval/compare', {
    params: { base_run_id: baseRunId, candidate_run_id: candidateRunId },
  })
  return data
}

export async function searchChunks(
  kbId: string,
  q: string,
  limit = 50,
): Promise<ChunkSearchItem[]> {
  const { data } = await api.get<ChunkSearchItem[]>(`/kbs/${kbId}/chunks`, {
    params: { q: q || undefined, limit },
  })
  return data
}
