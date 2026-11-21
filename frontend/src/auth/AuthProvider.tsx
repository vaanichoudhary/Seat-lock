import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, getTokens, setTokens, type Tokens, type User } from '../api'
import { AuthContext } from './AuthContext'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(() => getTokens() !== null)

  // on first load: if a token was saved, ask the backend who it belongs to
  useEffect(() => {
    if (!getTokens()) return
    api<User>('/auth/me')
      .then(setUser)
      .catch(() => setTokens(null)) // saved token no longer works
      .finally(() => setLoading(false))
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const tokens = await api<Tokens>('/auth/login', { method: 'POST', body: { email, password } })
    setTokens(tokens)
    setUser(await api<User>('/auth/me'))
  }, [])

  const register = useCallback(
    async (username: string, email: string, password: string) => {
      await api<User>('/auth/register', { method: 'POST', body: { username, email, password } })
      await login(email, password)
    },
    [login],
  )

  const logout = useCallback(() => {
    setTokens(null)
    setUser(null)
  }, [])

  const value = useMemo(
    () => ({ user, loading, login, register, logout }),
    [user, loading, login, register, logout],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
