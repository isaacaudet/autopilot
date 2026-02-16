import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import { fetchConfig } from '@/lib/api'
import type { ConfigData } from '@/lib/types'

type ChannelKey = string

interface ChannelScopeValue {
  channel: ChannelKey // 'all' or a concrete channel key from config
  setChannel: (key: ChannelKey) => void
  channels: Record<string, { name?: string }> | null
  loading: boolean
}

const ChannelScopeContext = createContext<ChannelScopeValue>({
  channel: 'all',
  setChannel: () => {},
  channels: null,
  loading: true,
})

const STORAGE_KEY = 'clipper.workspaceChannel'

function normalizeChannelKey(value: string | null | undefined): string {
  const v = String(value ?? '').trim()
  if (!v) return 'all'
  if (v.toLowerCase() === 'all' || v === '*') return 'all'
  return v
}

export function useChannelScopeProvider(): ChannelScopeValue {
  const [channel, setChannelState] = useState<string>(() => {
    try {
      return normalizeChannelKey(localStorage.getItem(STORAGE_KEY))
    } catch {
      return 'all'
    }
  })
  const [channels, setChannels] = useState<Record<string, { name?: string }> | null>(null)
  const [loading, setLoading] = useState(true)

  function setChannel(next: string) {
    const normalized = normalizeChannelKey(next)
    setChannelState(normalized)
    try {
      localStorage.setItem(STORAGE_KEY, normalized)
    } catch {
      // ignore storage failures (private mode, etc.)
    }
  }

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetchConfig()
      .then((cfg: ConfigData) => {
        if (cancelled) return
        const ch = (cfg.channels ?? null) as unknown as Record<string, { name?: string }> | null
        setChannels(ch)

        // If we have no persisted choice (or an invalid one), pick a sane default.
        const keys = ch ? Object.keys(ch) : []
        if (keys.length > 0) {
          const valid = channel === 'all' || keys.includes(channel)
          if (!valid) {
            const fallback = keys.includes('default') ? 'default' : keys[0]
            setChannel(fallback)
          }

          // First run: prefer the "default" channel if present.
          try {
            const stored = localStorage.getItem(STORAGE_KEY)
            if (!stored) {
              const fallback = keys.includes('default') ? 'default' : keys[0]
              setChannelState(fallback)
              localStorage.setItem(STORAGE_KEY, fallback)
            }
          } catch {
            // ignore
          }
        }
      })
      .catch(() => {
        if (!cancelled) setChannels(null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return useMemo(
    () => ({
      channel,
      setChannel,
      channels,
      loading,
    }),
    [channel, channels, loading],
  )
}

export function useChannelScope(): ChannelScopeValue {
  return useContext(ChannelScopeContext)
}

export { ChannelScopeContext }

