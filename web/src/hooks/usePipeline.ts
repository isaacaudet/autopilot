import { createContext, useContext, useCallback, useEffect, useRef, useState } from 'react'
import type { PipelineSnapshot } from '../lib/types'

interface PipelineContextValue {
  state: PipelineSnapshot | null
  connected: boolean
}

export const PipelineContext = createContext<PipelineContextValue>({
  state: null,
  connected: false,
})

export function usePipelineProvider(): PipelineContextValue {
  const [state, setState] = useState<PipelineSnapshot | null>(null)
  const [connected, setConnected] = useState(false)
  const esRef = useRef<EventSource | null>(null)
  const retryRef = useRef(0)

  const connect = useCallback(() => {
    if (esRef.current) {
      esRef.current.close()
    }

    const es = new EventSource('/api/workflow/stream')
    esRef.current = es

    es.onopen = () => {
      setConnected(true)
      retryRef.current = 0
    }
    es.onmessage = (e) => {
      try {
        setState(JSON.parse(e.data))
      } catch {
        // ignore malformed messages
      }
    }
    es.onerror = () => {
      setConnected(false)
      es.close()
      esRef.current = null
      // Reconnect with backoff (max 5s)
      const delay = Math.min(1000 * Math.pow(1.5, retryRef.current), 5000)
      retryRef.current += 1
      setTimeout(connect, delay)
    }
  }, [])

  useEffect(() => {
    connect()
    return () => {
      if (esRef.current) {
        esRef.current.close()
        esRef.current = null
      }
    }
  }, [connect])

  return { state, connected }
}

export function usePipeline(): PipelineContextValue {
  return useContext(PipelineContext)
}
