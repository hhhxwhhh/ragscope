import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'

const TOKEN_KEY = 'ragscope_access_token'

export const tokenStorage = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (token: string) => localStorage.setItem(TOKEN_KEY, token),
  clear: () => localStorage.removeItem(TOKEN_KEY),
}

export const api = axios.create({
  baseURL: '/api/v1',
  timeout: 30_000,
  withCredentials: true,
})

api.interceptors.request.use((config) => {
  const token = tokenStorage.get()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

type RetryConfig = InternalAxiosRequestConfig & { _retry?: boolean }

let refreshPromise: Promise<string> | null = null

async function refreshAccessToken(): Promise<string> {
  // Raw axios: must bypass the interceptor that is currently waiting on this call.
  const { data } = await axios.post<{ access_token: string }>(
    '/api/v1/auth/refresh',
    null,
    { withCredentials: true },
  )
  tokenStorage.set(data.access_token)
  return data.access_token
}

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<{ detail?: string }>) => {
    const config = error.config as RetryConfig | undefined
    const status = error.response?.status
    const url = config?.url ?? ''
    const isAuthCall =
      url.includes('/auth/login') ||
      url.includes('/auth/register') ||
      url.includes('/auth/refresh')

    if (status === 401 && config && !config._retry && !isAuthCall) {
      config._retry = true
      try {
        // Single-flight: concurrent 401s share one refresh request.
        refreshPromise ??= refreshAccessToken().finally(() => {
          refreshPromise = null
        })
        const token = await refreshPromise
        config.headers.Authorization = `Bearer ${token}`
        return api(config)
      } catch {
        tokenStorage.clear()
        localStorage.removeItem('ragscope_user')
        if (!window.location.pathname.startsWith('/login')) {
          window.location.assign('/login')
        }
        return Promise.reject(error)
      }
    }

    if (status !== 401) {
      const message = error.response?.data?.detail || error.message || '请求失败'
      ElMessage.error(message)
    }
    return Promise.reject(error)
  },
)
