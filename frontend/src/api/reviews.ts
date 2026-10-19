import { api } from './client'
import type {
  AnswerReview,
  FeedbackCaseResult,
  ReviewIssueTag,
  ReviewQueuePage,
  ReviewSummary,
  ReviewVerdict,
} from '@/types'

export interface ReviewPayload {
  verdict: ReviewVerdict
  issue_tags: ReviewIssueTag[]
  note?: string | null
}

export interface ReviewQueueParams {
  status?: 'unreviewed' | 'reviewed' | 'all'
  verdict?: ReviewVerdict | ''
  q?: string
  offset?: number
  limit?: number
}

export async function listReviewQueue(
  kbId: string,
  params: ReviewQueueParams = {},
): Promise<ReviewQueuePage> {
  const { data } = await api.get<ReviewQueuePage>(`/kbs/${kbId}/reviews/queue`, {
    params: {
      status: params.status ?? 'unreviewed',
      verdict: params.verdict || undefined,
      q: params.q || undefined,
      offset: params.offset ?? 0,
      limit: params.limit ?? 20,
    },
  })
  return data
}

export async function getReviewSummary(kbId: string): Promise<ReviewSummary> {
  const { data } = await api.get<ReviewSummary>(`/kbs/${kbId}/reviews/summary`)
  return data
}

export async function upsertReview(
  messageId: string,
  payload: ReviewPayload,
): Promise<AnswerReview> {
  const { data } = await api.put<AnswerReview>(
    `/messages/${messageId}/review`,
    payload,
  )
  return data
}

export async function deleteReview(messageId: string): Promise<void> {
  await api.delete(`/messages/${messageId}/review`)
}

export async function convertReviewToCase(reviewId: string): Promise<FeedbackCaseResult> {
  const { data } = await api.post<FeedbackCaseResult>(`/reviews/${reviewId}/eval-case`)
  return data
}
