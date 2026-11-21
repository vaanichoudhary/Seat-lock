import { useEffect, useState } from 'react'

// The current time, as state that refreshes every `intervalMs`.
// Components must not read the clock (Date.now()) while rendering, because then the
// same render could give different results. Reading it from state keeps them predictable.
export function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(timer) // stop the timer when the component goes away
  }, [intervalMs])
  return now
}
