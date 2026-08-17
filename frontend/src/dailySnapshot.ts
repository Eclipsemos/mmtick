const SNAPSHOT_VERSION = 1
const SNAPSHOT_PREFIX = 'mmtick:daily-snapshot'

type StoredSnapshot<T> = {
  version: number
  utcDay: string
  savedAtMs: number
  value: T
}

export function utcSnapshotDay(nowMs = Date.now()) {
  return new Date(nowMs).toISOString().slice(0, 10)
}

export function millisecondsUntilNextUtcDay(nowMs = Date.now()) {
  const now = new Date(nowMs)
  const next = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1)
  return Math.max(1, next - nowMs)
}

export function readDailySnapshot<T>(scope: string, utcDay: string): T | undefined {
  const snapshot = readStoredSnapshot<T>(scope)
  return snapshot?.utcDay === utcDay ? snapshot.value : undefined
}

export function readLatestDailySnapshot<T>(scope: string): T | undefined {
  return readStoredSnapshot<T>(scope)?.value
}

export function writeDailySnapshot<T>(scope: string, utcDay: string, value: T) {
  if (typeof window === 'undefined') return
  const snapshot: StoredSnapshot<T> = {
    version: SNAPSHOT_VERSION,
    utcDay,
    savedAtMs: Date.now(),
    value,
  }
  try {
    window.localStorage.setItem(storageKey(scope), JSON.stringify(snapshot))
  } catch {
    // A disabled or full browser storage must not prevent the live API response from rendering.
  }
}

export async function getDailySnapshot<T>(
  scope: string,
  utcDay: string,
  load: () => Promise<T>,
) {
  const cached = readDailySnapshot<T>(scope, utcDay)
  if (cached !== undefined) return cached
  const value = await load()
  writeDailySnapshot(scope, utcDay, value)
  return value
}

function storageKey(scope: string) {
  return `${SNAPSHOT_PREFIX}:v${SNAPSHOT_VERSION}:${scope}`
}

function readStoredSnapshot<T>(scope: string): StoredSnapshot<T> | undefined {
  if (typeof window === 'undefined') return undefined
  try {
    const raw = window.localStorage.getItem(storageKey(scope))
    if (!raw) return undefined
    const snapshot = JSON.parse(raw) as StoredSnapshot<T>
    return snapshot.version === SNAPSHOT_VERSION ? snapshot : undefined
  } catch {
    return undefined
  }
}
