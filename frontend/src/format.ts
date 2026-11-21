export function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function formatMoney(amount: string | number): string {
  return `₹${Number(amount).toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`
}

// "B12" -> { row: "B", number: 12 }
export function splitSeatNumber(seatNumber: string): { row: string; number: number } {
  const match = /^([A-Z]+)(\d+)$/.exec(seatNumber)
  return match ? { row: match[1], number: Number(match[2]) } : { row: '?', number: 0 }
}
