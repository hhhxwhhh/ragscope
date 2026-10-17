export interface Page<T> {
  items: T[]
  total: number
  offset: number
  limit: number
}

export interface User {
  id: string
  email: string
  display_name: string | null
  created_at: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
  user: User
}

export interface LoginPayload {
  email: string
  password: string
}

export interface RegisterPayload extends LoginPayload {
  display_name?: string
}

export interface KnowledgeBase {
  id: string
  name: string
  description: string | null
  chunk_size: number
  chunk_overlap: number
  index_version: number
  retrieval_config: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface KnowledgeBaseCreate {
  name: string
  description?: string | null
  chunk_size?: number
  chunk_overlap?: number
}

export type DocumentStatus = 'pending' | 'parsing' | 'indexing' | 'indexed' | 'failed'

export interface DocumentItem {
  id: string
  kb_id: string
  filename: string
  ext: string
  size_bytes: number
  status: DocumentStatus
  error_stage: string | null
  error_message: string | null
  chunk_count: number
  indexed_at: string | null
  created_at: string
}

export interface ChunkItem {
  id: string
  chunk_index: number
  content: string
  heading_path: string | null
  page_number: number | null
  char_start: number
  char_end: number
}
