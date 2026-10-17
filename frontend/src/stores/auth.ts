import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import * as authApi from '@/api/auth'
import { tokenStorage } from '@/api/client'
import type { LoginPayload, RegisterPayload, TokenResponse, User } from '@/types'

const USER_KEY = 'ragscope_user'

function readStoredUser(): User | null {
  const raw = localStorage.getItem(USER_KEY)
  if (!raw) {
    return null
  }
  try {
    return JSON.parse(raw) as User
  } catch {
    return null
  }
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(tokenStorage.get())
  const user = ref<User | null>(readStoredUser())
  const isAuthenticated = computed(() => Boolean(token.value))

  function applySession(session: TokenResponse): void {
    token.value = session.access_token
    tokenStorage.set(session.access_token)
    user.value = session.user
    localStorage.setItem(USER_KEY, JSON.stringify(session.user))
  }

  function clearSession(): void {
    token.value = null
    user.value = null
    tokenStorage.clear()
    localStorage.removeItem(USER_KEY)
  }

  async function login(payload: LoginPayload): Promise<void> {
    applySession(await authApi.login(payload))
  }

  async function register(payload: RegisterPayload): Promise<void> {
    applySession(await authApi.register(payload))
  }

  async function loadProfile(): Promise<void> {
    if (!token.value) {
      return
    }
    user.value = await authApi.fetchMe()
    localStorage.setItem(USER_KEY, JSON.stringify(user.value))
  }

  async function logout(): Promise<void> {
    try {
      await authApi.logout()
    } finally {
      clearSession()
    }
  }

  return { token, user, isAuthenticated, login, register, loadProfile, logout, clearSession }
})
