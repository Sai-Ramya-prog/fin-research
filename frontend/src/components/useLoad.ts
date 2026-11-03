import { useEffect, useState } from 'react'

/** Run an async loader once and track loading / error. */
export function useLoad<T>(loader: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    setLoading(true)
    loader()
      .then(d => { if (alive) { setData(d); setError(null) } })
      .catch(e => { if (alive) setError(e instanceof Error ? e.message : 'Failed to load') })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return { data, error, loading }
}
