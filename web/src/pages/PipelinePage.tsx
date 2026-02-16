import { useState, useEffect } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Checkbox } from '@/components/ui/checkbox'
import { WorkerStatus } from '@/components/WorkerStatus'
import { VideoPreview } from '@/components/VideoPreview'
import { CompilationReview } from '@/components/CompilationReview'
import { usePipeline } from '@/hooks/usePipeline'
import { Play, Upload, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { fetchQueue, uploadBatch } from '@/lib/api'
import type { ClipMeta } from '@/lib/types'
import { useChannelScope } from '@/hooks/useChannelScope'

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

const phaseLabels: Record<string, string> = {
  starting: 'Starting',
  learning: 'Learning from YouTube',
  fetching: 'Fetching clips',
  scoring: 'Scoring & ranking',
  approving: 'Approving clips',
  processing: 'Processing clips',
  compiling: 'Compiling video',
  uploading: 'Uploading',
  done: 'Complete',
  error: 'Error',
}

export function PipelinePage() {
  const { state, connected } = usePipeline()
  const [previewClipId, setPreviewClipId] = useState<string | null>(null)
  const [previewTitle, setPreviewTitle] = useState('')

  const phase = state?.phase || ''
  const isRunning = state?.running === true
  const isComplete = phase === 'done'
  const isError = phase === 'error'
  const isIdle = !phase || (!isRunning && !isComplete && !isError)
  const isPreProcessing = isRunning && ['starting', 'learning', 'fetching', 'scoring', 'approving'].includes(phase)

  const pct = state && state.total > 0
    ? Math.round(((state.completed + state.failed) / state.total) * 100)
    : 0

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold tracking-tight">Pipeline</h1>
        {!connected && <Badge variant="outline">Reconnecting...</Badge>}
        {connected && isIdle && <Badge variant="outline">Idle</Badge>}
        {isRunning && <Badge className="bg-primary text-primary-foreground animate-pulse">Running</Badge>}
        {isComplete && <Badge variant="secondary">Complete</Badge>}
        {isError && <Badge variant="destructive">Error</Badge>}
      </div>

      {/* Phase indicator */}
      {state && phase && (
        <div className="rounded-lg border p-4 space-y-1">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">
              {phaseLabels[phase] || phase}
            </span>
            {state.elapsed > 0 && (
              <span className="text-xs text-muted-foreground">
                {formatTime(state.elapsed)} elapsed
              </span>
            )}
          </div>
          {state.phase_detail && (
            <p className="text-sm text-muted-foreground">{state.phase_detail}</p>
          )}
          {isPreProcessing && (
            <div className="pt-1">
              <Progress value={undefined} className="h-2 [&>div]:bg-primary animate-pulse" />
            </div>
          )}
        </div>
      )}

      {/* Processing progress */}
      {state && (phase === 'processing' || isComplete || isError) && state.total > 0 && (
        <>
          <div className="space-y-2">
            <div className="flex items-center justify-between text-sm">
              <span>
                {state.completed} / {state.total} completed
                {state.failed > 0 && (
                  <span className="text-destructive ml-2">({state.failed} failed)</span>
                )}
              </span>
              <span className="text-muted-foreground">
                {formatTime(state.elapsed)} elapsed
                {state.eta !== null && state.eta > 0 && ` / ~${formatTime(state.eta)} remaining`}
              </span>
            </div>
            <Progress value={pct} className="h-3 [&>div]:bg-primary" />
            <div className="text-right text-xs text-muted-foreground">{pct}%</div>
          </div>

          {Object.keys(state.workers).length > 0 && (
            <div className="space-y-2">
              <h2 className="text-sm font-medium uppercase tracking-wider text-muted-foreground">Workers</h2>
              <div className="space-y-1">
                {Object.entries(state.workers).map(([label, [clipTitle, step, startedAt]]) => (
                  <div key={label} className="rounded-md border px-3 py-2">
                    <div className="text-xs text-muted-foreground font-mono mb-1">{label}</div>
                    <WorkerStatus clipTitle={clipTitle} step={step} startedAt={startedAt} />
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="grid gap-4 md:grid-cols-2">
            {state.completed_clips.length > 0 && (
              <div className="space-y-2">
                <h2 className="text-sm font-semibold text-green-400">Completed</h2>
                <ScrollArea className="h-48 rounded-md border p-3">
                  {state.completed_clips.map((filename, i) => {
                    const clipId = state.completed_clip_ids?.[i]
                    return clipId ? (
                      <button
                        key={i}
                        onClick={() => { setPreviewClipId(clipId); setPreviewTitle(filename) }}
                        className="flex items-center gap-2 w-full text-left text-sm py-0.5 font-mono truncate hover:text-primary transition-colors cursor-pointer"
                      >
                        <Play className="size-3 shrink-0 text-muted-foreground" />
                        {filename}
                      </button>
                    ) : (
                      <div key={i} className="text-sm py-0.5 font-mono truncate">{filename}</div>
                    )
                  })}
                </ScrollArea>
              </div>
            )}

            {state.errors.length > 0 && (
              <div className="space-y-2">
                <h2 className="text-sm font-semibold text-destructive">Errors</h2>
                <ScrollArea className="h-48 rounded-md border p-3">
                  {state.errors.map((e, i) => (
                    <div key={i} className="text-sm py-0.5 font-mono truncate text-destructive">{e}</div>
                  ))}
                </ScrollArea>
              </div>
            )}
          </div>
        </>
      )}

      {/* Compile progress */}
      {state && phase === 'compiling' && (
        <div className="space-y-2">
          <div className="flex items-center justify-between text-sm">
            <span>Compiling: {state.compile_step || 'preparing'}</span>
            <span className="text-muted-foreground">{Math.round((state.compile_progress ?? 0) * 100)}%</span>
          </div>
          <Progress value={(state.compile_progress ?? 0) * 100} className="h-3 [&>div]:bg-primary" />
        </div>
      )}

      {/* Review phase — shown after processing completes */}
      {isComplete && state?.recipe && ['compilation', 'snipe'].includes(state.recipe) && (
        <CompilationReview />
      )}

      {isComplete && state?.recipe === 'shorts' && (
        <ShortsReview completedClipIds={state.completed_clip_ids ?? []} />
      )}

      {isIdle && connected && (
        <p className="text-muted-foreground">
          No active pipeline. Start a workflow from the Dashboard.
        </p>
      )}

      <VideoPreview
        clipId={previewClipId}
        title={previewTitle}
        open={previewClipId !== null}
        onOpenChange={(open) => { if (!open) setPreviewClipId(null) }}
      />
    </div>
  )
}

// ─── Shorts Review ────────────────────────────────────────────────

function ShortsReview({ completedClipIds }: { completedClipIds: string[] }) {
  const [clips, setClips] = useState<ClipMeta[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [uploading, setUploading] = useState(false)
  const { channel: workspaceChannel } = useChannelScope()

  useEffect(() => {
    fetchQueue('output', { channel: workspaceChannel !== 'all' ? workspaceChannel : undefined })
      .then((all) => {
        const idSet = new Set(completedClipIds)
        const relevant = all.filter((c) => idSet.has(c.id))
        setClips(relevant)
        setSelected(new Set(relevant.map((c) => c.id)))
      })
      .catch(() => {})
  }, [completedClipIds, workspaceChannel])

  function toggleClip(id: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function handleUpload() {
    const ids = [...selected]
    if (ids.length === 0) return
    setUploading(true)
    try {
      const targetChannel = workspaceChannel === 'all' ? null : workspaceChannel
      await uploadBatch(ids, 'unlisted', targetChannel)
      toast.success(`Uploaded ${ids.length} clips`)
    } catch {
      toast.error('Upload failed')
    } finally {
      setUploading(false)
    }
  }

  if (clips.length === 0) return null

  return (
    <div className="space-y-3">
      <h2 className="text-lg font-semibold">Review Shorts</h2>
      <div className="rounded-md border divide-y">
        {clips.map((clip) => (
          <label
            key={clip.id}
            className="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-accent"
          >
            <Checkbox
              checked={selected.has(clip.id)}
              onCheckedChange={() => toggleClip(clip.id)}
            />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate">{clip.title}</div>
              <div className="text-xs text-muted-foreground">
                {clip.streamer} &middot; {(clip.duration ?? 0).toFixed(1)}s
              </div>
            </div>
            {clip.video_id && (
              <Badge variant="secondary" className="shrink-0">uploaded</Badge>
            )}
          </label>
        ))}
      </div>
      <Button onClick={handleUpload} disabled={uploading || selected.size === 0}>
        {uploading ? (
          <><Loader2 className="size-4 animate-spin mr-1" />Uploading...</>
        ) : (
          <><Upload className="size-4 mr-1" />Upload Selected ({selected.size})</>
        )}
      </Button>
    </div>
  )
}
