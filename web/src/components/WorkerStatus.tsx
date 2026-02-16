import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import { CheckCircle2, XCircle } from 'lucide-react'

const STEPS = ['DL', 'TR', 'FMT', 'BURN']

const stepIndex: Record<string, number> = {
  downloading: 0,
  transcribing: 1,
  formatting: 2,
  burn: 3,
  burning: 3,
  done: 4,
  error: -1,
}

interface WorkerStatusProps {
  clipTitle: string
  step: string
  startedAt: number
}

export function WorkerStatus({ clipTitle, step, startedAt }: WorkerStatusProps) {
  const current = stepIndex[step] ?? -1
  const isDone = step === 'done'
  const isError = step === 'error'
  const isActive = !isDone && !isError

  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    if (!isActive || !startedAt) {
      setElapsed(0)
      return
    }
    const tick = () => setElapsed(Math.floor(Date.now() / 1000 - startedAt))
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [isActive, startedAt])

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1">
          {STEPS.map((s, i) => (
            <span
              key={s}
              className={cn(
                'inline-flex items-center justify-center rounded px-1.5 py-0.5 text-xs font-mono font-medium',
                i < current && 'bg-green-900/50 text-green-400',
                i === current && 'bg-primary/15 text-primary',
                i > current && 'bg-muted text-muted-foreground',
                isError && 'bg-red-900/50 text-red-400',
              )}
            >
              {s}
            </span>
          ))}
        </div>
        {isDone && <CheckCircle2 className="size-4 text-green-400" />}
        {isError && <XCircle className="size-4 text-destructive" />}
        {isActive && elapsed > 0 && (
          <span className="text-xs font-mono text-muted-foreground">{elapsed}s</span>
        )}
        <span className="text-sm truncate max-w-64 text-muted-foreground">{clipTitle}</span>
      </div>
      {isActive && (
        <div
          className="h-0.5 w-full rounded-full bg-muted overflow-hidden"
          role="progressbar"
          aria-label={`Processing: ${step}`}
          aria-valuemin={0}
          aria-valuemax={STEPS.length}
          aria-valuenow={current >= 0 ? current : 0}
        >
          <div className="h-full w-1/3 rounded-full bg-primary animate-pulse" />
        </div>
      )}
    </div>
  )
}
