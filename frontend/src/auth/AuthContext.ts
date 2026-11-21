import { createContext, useContext } from 'react'
import type { User } from '../api'

export type AuthState = {
  user: User | null
  loading: boolean // true while we check a saved token on first load
  login: (email: string, password: string) => Promise<void>
  register: (username: string, email: string, password: string) => Promise<void>
  logout: () => void
}

// A "context" is a value every component can read without passing it down as props.
export const AuthContext = createContext<AuthState | null>(null)

export function useAuth(): AuthState {
  const auth = useContext(AuthContext)
  if (!auth) throw new Error('useAuth must be used inside <AuthProvider>')
  return auth
}
