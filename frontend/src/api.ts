// Everything that talks to the backend lives here, so pages never call fetch() directly.

// ---------- shapes of the data the backend sends (mirror of the Pydantic schemas) ----------
export type Role = 'user' | 'organizer' | 'admin'
export type User = { id: number; username: string; email: string; role: Role }
export type Tokens = { access_token: string; refresh_token: string }

export type EventT = {
  id: number
  organizer_id: number
  name: string
  description: string | null
  photo_url: string | null
  starts_at: string
  sale_opens_at: string
  ticket_price: string
}

export type SeatStatus = 'available' | 'held' | 'booked'
export type Seat = { id: number; event_id: number; seat_number: string; status: SeatStatus }
export type MyHold = { seat_ids: number[]; held_until: string | null }

export type BookingStatus = 'pending' | 'confirmed' | 'failed' | 'expired' | 'cancelled'
export type Booking = {
  id: number
  event_id: number
  status: BookingStatus
  total_amount: string
  expires_at: string
  created_at: string
  seats: { seat_id: number; seat_number: string; price: string }[]
}
export type Payment = { id: number; booking_id: number; amount: string; status: string; gateway_ref: string }
export type GatewayResult = { payment_status: string; booking_status: BookingStatus }

// ---------- errors ----------
export class ApiError extends Error {
  status: number
  detail: unknown

  constructor(status: number, detail: unknown) {
    super(messageFrom(detail))
    this.status = status
    this.detail = detail
  }
}

// FastAPI sends errors in a few shapes; turn any of them into one readable sentence
function messageFrom(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    // 422 validation errors: [{loc: [...], msg: "..."}]
    return detail.map((d: { msg?: string }) => d.msg ?? 'Invalid input').join('; ')
  }
  if (detail && typeof detail === 'object' && 'message' in detail) return String(detail.message)
  return 'Something went wrong'
}

// ---------- tokens ----------
// Kept in localStorage so you stay logged in after a refresh. Trade-off: any script
// running on the page could read them (an XSS attack). Real apps often keep the refresh
// token in an httpOnly cookie instead; localStorage keeps this project simple.
const TOKENS_KEY = 'seatlock.tokens'

export function getTokens(): Tokens | null {
  const raw = localStorage.getItem(TOKENS_KEY)
  return raw ? (JSON.parse(raw) as Tokens) : null
}

export function setTokens(tokens: Tokens | null) {
  if (tokens) localStorage.setItem(TOKENS_KEY, JSON.stringify(tokens))
  else localStorage.removeItem(TOKENS_KEY)
}

// If several requests hit "token expired" at the same moment, they all wait
// for ONE refresh call instead of each sending their own.
let refreshing: Promise<boolean> | null = null

function refreshTokens(): Promise<boolean> {
  refreshing ??= (async () => {
    const tokens = getTokens()
    if (!tokens) return false
    const r = await fetch('/api/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: tokens.refresh_token }),
    })
    if (!r.ok) {
      setTokens(null) // refresh token is dead too: you are logged out
      return false
    }
    setTokens(await r.json())
    return true
  })().finally(() => {
    refreshing = null
  })
  return refreshing
}

// ---------- the one function every page uses ----------
type Options = { method?: string; body?: unknown; headers?: Record<string, string> }

export async function api<T>(path: string, options: Options = {}, retried = false): Promise<T> {
  const tokens = getTokens()
  const headers: Record<string, string> = { ...options.headers }
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'
  if (tokens) headers['Authorization'] = `Bearer ${tokens.access_token}`

  const r = await fetch(`/api${path}`, {
    method: options.method ?? 'GET',
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  })

  // access token expired (they only live 15 minutes): get a new one and try once more
  if (r.status === 401 && tokens && !retried && (await refreshTokens())) {
    return api<T>(path, options, true)
  }

  const data = r.status === 204 ? null : await r.json().catch(() => null)
  if (!r.ok) throw new ApiError(r.status, data?.detail)
  return data as T
}
