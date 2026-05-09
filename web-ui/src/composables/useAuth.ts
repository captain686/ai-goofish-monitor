import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { wsService } from '@/services/websocket'

const username = ref<string | null>(localStorage.getItem('auth_username'))
const isLoggedIn = ref(localStorage.getItem('auth_logged_in') === 'true')

async function fetchAuthStatus(): Promise<{ authenticated: boolean; username?: string }> {
  try {
    const response = await fetch('/auth/me', {
      method: 'GET',
      credentials: 'include',
    })
    if (!response.ok) {
      return { authenticated: false }
    }
    return await response.json()
  } catch {
    return { authenticated: false }
  }
}

export function useAuth() {
  const router = useRouter()

  const isAuthenticated = computed(() => isLoggedIn.value)

  function setAuthenticated(user: string) {
    username.value = user
    isLoggedIn.value = true
    localStorage.setItem('auth_username', user)
    localStorage.setItem('auth_logged_in', 'true')
    wsService.start()
  }

  async function refreshAuthStatus(): Promise<boolean> {
    const status = await fetchAuthStatus()
    if (status.authenticated) {
      setAuthenticated(status.username || 'admin')
      return true
    }
    await logout(false)
    return false
  }

  async function logout(redirect = true) {
    try {
      await fetch('/auth/logout', {
        method: 'POST',
        credentials: 'include',
      })
    } catch {
      // ignore network error on logout
    }

    username.value = null
    isLoggedIn.value = false
    localStorage.removeItem('auth_username')
    localStorage.removeItem('auth_logged_in')
    wsService.stop()

    if (redirect) {
      if (router) {
        router.push('/login')
      } else {
        window.location.href = '/login'
      }
    }
  }

  async function login(user: string, pass: string): Promise<boolean> {
    try {
      const response = await fetch('/auth/login', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include',
        body: JSON.stringify({ username: user, password: pass }),
      })

      if (!response.ok) return false
      const data = await response.json()
      setAuthenticated(data.username || user)
      return true
    } catch (e) {
      console.error('Login error', e)
      return false
    }
  }

  return {
    username,
    isAuthenticated,
    login,
    logout,
    refreshAuthStatus,
  }
}
