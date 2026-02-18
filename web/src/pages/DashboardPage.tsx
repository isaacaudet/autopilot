import { type ComponentType, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Progress } from '@/components/ui/progress'
import {
  Activity,
  Calendar,
  FolderOpen,
  ListVideo,
  Loader2,
  Sparkles,
  Upload,
  Wrench,
  Zap,
} from 'lucide-react'
import { toast } from 'sonner'
import {
  fetchQueue,
  fetchReleases,
  openOutputClip,
  openOutputFolder,
  resyncOutput,
  startAutopilot,
} from '@/lib/api'
import type { ClipMeta, Release } from '@/lib/types'
import { WorkflowDialog } from '@/components/WorkflowDialog'
import { usePipeline } from '@/hooks/usePipeline'
import { useChannelScope } from '@/hooks/useChannelScope'

const phaseLabels: Record<string, string> = {
  starting: 'Starting',
  learning: 'Learning',
  fetching: 'Fetching clips',
  scoring: 'Scoring',
  approving: 'Approving',
  processing: 'Processing',
  compiling: 'Compiling',
  uploading: 'Uploading',
  done: 'Complete',
  error: 'Error',
}

const workerStepLabels: Record<string, string> = {
  downloading: 'Downloading',
  trimming: 'Trimming',
  transcribing: 'Transcribing',
  formatting: 'Formatting',
  burning: 'Burning subtitles',
  done: 'Done',
  error: 'Error',
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

export function DashboardPage() {
  const [pending, setPending] = useState<ClipMeta[]>([])
  const [output, setOutput] = useState<ClipMeta[]>([])
  const [releases, setReleases] = useState<Release[]>([])
  const [loading, setLoading] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [dialogRecipe, setDialogRecipe] = useState<string | undefined>()
  const [autopilotCount, setAutopilotCount] = useState(8)
  const [autopilotMinScore, setAutopilotMinScore] = useState(45)
  const [autopilotLoading, setAutopilotLoading] = useState(false)
  const [resyncing, setResyncing] = useState(false)
  const { state: pipeline } = usePipeline()
  const { channel } = useChannelScope()
  const navigate = useNavigate()

  const isRunning = pipeline?.running === true
  const activeWorkers = useMemo(
    () => (pipeline ? Object.values(pipeline.workers).filter(([, step]) => step !== 'done' && step !== 'error').length : 0),
    [pipeline],
  )
  const activeWorkerRows = useMemo(
    () => (
      pipeline
        ? Object.entries(pipeline.workers)
          .filter(([, [, step]]) => step !== 'done' && step !== 'error')
          .slice(0, 5)
        : []
    ),
    [pipeline],
  )
  const pipelineProgress = useMemo(() => {
    if (!pipeline || pipeline.total <= 0) return null
    const processed = pipeline.completed + pipeline.failed
    const pct = Math.max(0, Math.min(100, Math.round((processed / pipeline.total) * 100)))
    return { processed, pct }
  }, [pipeline])

  async function load() {
    setLoading(true)
    try {
      const channelKey = channel !== 'all' ? channel : undefined
      const [pendingClips, outputClips, releaseRows] = await Promise.all([
        fetchQueue('pending', { sort: 'recent', channel: channelKey }),
        fetchQueue('output', { sort: 'recent', channel: channelKey }),
        fetchReleases(channelKey),
      ])

      setPending(pendingClips)
      setOutput(outputClips)
      setReleases(releaseRows)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load().catch(() => {})
  }, [channel])

  async function handleAutopilot() {
    setAutopilotLoading(true)
    try {
      const targetChannel = channel === 'all' ? 'default' : channel
      await startAutopilot({
        count: autopilotCount,
        min_score: autopilotMinScore,
        channel: targetChannel,
        game: 'Deadlock',
        auto_upload: true,
        privacy: 'private',
      })
      toast.success('Autopilot started')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Autopilot failed to start')
    } finally {
      setAutopilotLoading(false)
    }
  }

  async function handleResync() {
    setResyncing(true)
    try {
      const result = await resyncOutput(300)
      toast.success(`Reindexed ${result.created} output clips`)
      await load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Output reindex failed')
    } finally {
      setResyncing(false)
    }
  }

  async function handleOpenOutputFolder() {
    try {
      await openOutputFolder()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to open output folder')
    }
  }

  function openWorkflow(recipe?: string) {
    setDialogRecipe(recipe)
    setDialogOpen(true)
  }

  const readyCount = output.filter((c) => !c.video_id).length
  const uploadedCount = output.filter((c) => !!c.video_id).length
  const scheduledCount = releases.filter((r) => r.status === 'pending').length

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold tracking-tight">Control Center</h1>
          <p className="text-sm text-muted-foreground">
            Run workflows, monitor processing, and ship clips from one screen.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={handleOpenOutputFolder}>
            <FolderOpen className="size-4 mr-1" />
            Output Folder
          </Button>
          <Button variant="outline" onClick={() => navigate('/studio')}>Open Studio</Button>
          <Button variant="outline" onClick={load} disabled={loading}>
            {loading ? <Loader2 className="size-4 animate-spin" /> : 'Refresh'}
          </Button>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard title="Queued" value={pending.length} subtitle="pending clips" icon={ListVideo} />
        <StatCard title="Processing" value={activeWorkers} subtitle={isRunning ? 'workers active' : 'idle'} icon={Activity} pulse={isRunning} />
        <StatCard title="Ready Output" value={readyCount} subtitle={`${uploadedCount} uploaded`} icon={Upload} />
        <StatCard title="Scheduled" value={scheduledCount} subtitle="releases pending" icon={Calendar} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.2fr_1fr]">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <Sparkles className="size-4 text-primary" />
              Quick Actions
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={() => openWorkflow('shorts')}>Quick Shorts</Button>
              <Button variant="outline" onClick={() => openWorkflow('compilation')}>Compilation</Button>
              <Button variant="outline" onClick={() => openWorkflow('snipe')}>Daily Snipe</Button>
            </div>

            <div className="rounded-lg border bg-muted/20 p-3 space-y-3">
              <p className="text-sm text-muted-foreground">
                Autopilot runs discover + scoring + processing in one batch.
              </p>
              <div className="flex flex-wrap items-end gap-3">
                <div className="space-y-1">
                  <label className="text-xs text-muted-foreground">Count</label>
                  <Input
                    type="number"
                    min={1}
                    max={20}
                    value={autopilotCount}
                    onChange={(e) => setAutopilotCount(Number(e.target.value))}
                    className="w-20 h-8"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs text-muted-foreground">Min Score</label>
                  <Input
                    type="number"
                    min={0}
                    max={100}
                    value={autopilotMinScore}
                    onChange={(e) => setAutopilotMinScore(Number(e.target.value))}
                    className="w-20 h-8"
                  />
                </div>
                <Button onClick={handleAutopilot} disabled={autopilotLoading}>
                  {autopilotLoading ? <Loader2 className="size-4 animate-spin mr-1" /> : <Zap className="size-4 mr-1" />}
                  Run Autopilot
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <Activity className="size-4 text-primary" />
              Pipeline Monitor
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center justify-between">
              <p className="text-sm font-medium">
                {pipeline?.phase ? phaseLabels[pipeline.phase] || pipeline.phase : 'Idle'}
              </p>
              <Badge variant={isRunning ? 'default' : 'outline'}>
                {isRunning ? 'running' : 'idle'}
              </Badge>
            </div>
            {pipeline?.phase_detail && (
              <p className="text-xs text-muted-foreground">{pipeline.phase_detail}</p>
            )}
            {pipeline && pipeline.total > 0 && (
              <p className="text-xs text-muted-foreground">
                {pipeline.completed}/{pipeline.total} completed
                {pipeline.failed > 0 ? ` · ${pipeline.failed} failed` : ''}
              </p>
            )}
            {pipelineProgress && (
              <div className="space-y-1.5">
                <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                  <span>{pipelineProgress.processed}/{pipeline?.total ?? 0} processed</span>
                  <span>{pipelineProgress.pct}%</span>
                </div>
                <Progress value={pipelineProgress.pct} className="h-2 [&>div]:bg-primary" />
                <div className="text-[11px] text-muted-foreground">
                  {pipeline && pipeline.elapsed > 0 ? `${formatTime(pipeline.elapsed)} elapsed` : '0:00 elapsed'}
                  {pipeline?.eta !== null && pipeline?.eta && pipeline.eta > 0 ? ` · ~${formatTime(pipeline.eta)} left` : ''}
                </div>
              </div>
            )}
            {pipeline?.phase === 'compiling' && typeof pipeline.compile_progress === 'number' && (
              <div className="space-y-1.5">
                <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                  <span>Compile: {pipeline.compile_step || 'working'}</span>
                  <span>{Math.round((pipeline.compile_progress || 0) * 100)}%</span>
                </div>
                <Progress value={(pipeline.compile_progress || 0) * 100} className="h-1.5 [&>div]:bg-primary" />
              </div>
            )}
            {pipeline?.phase === 'uploading' && (pipeline.uploads_total ?? 0) > 0 && (
              <div className="text-[11px] text-muted-foreground">
                Uploads: {pipeline.uploads_done ?? 0}/{pipeline.uploads_total ?? 0}
              </div>
            )}
            {activeWorkerRows.length > 0 && (
              <div className="rounded-lg border bg-muted/10">
                {activeWorkerRows.map(([label, [clipTitle, step]]) => (
                  <div key={label} className="border-b last:border-b-0 px-3 py-2">
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-xs font-medium truncate">{clipTitle || 'Untitled clip'}</p>
                        <p className="text-[11px] text-muted-foreground font-mono">{label}</p>
                      </div>
                      <Badge variant="outline" className="shrink-0">
                        {workerStepLabels[step] || step}
                      </Badge>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {pipeline?.errors?.length ? (
              <div className="rounded-md border border-destructive/25 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                Last error: {pipeline.errors[pipeline.errors.length - 1]}
              </div>
            ) : null}
            <div className="flex gap-2">
              {(isRunning || pipeline?.phase === 'done' || pipeline?.phase === 'error') && (
                <Button variant="default" onClick={() => navigate('/pipeline')}>View Pipeline</Button>
              )}
              <Button variant="outline" onClick={() => navigate('/studio')}>Review Output</Button>
              <Button variant="outline" onClick={handleResync} disabled={resyncing}>
                {resyncing ? <Loader2 className="size-4 animate-spin mr-1" /> : <Wrench className="size-4 mr-1" />}
                Reindex Output
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>

      <div>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Latest Output</CardTitle>
          </CardHeader>
          <CardContent>
            {output.length === 0 ? (
              <div className="space-y-3">
                <p className="text-sm text-muted-foreground">No output records found yet.</p>
                <Button variant="outline" onClick={handleResync} disabled={resyncing}>
                  {resyncing ? <Loader2 className="size-4 animate-spin mr-1" /> : null}
                  Reindex from output folder
                </Button>
              </div>
            ) : (
              <div className="space-y-2">
                {output.slice(0, 8).map((clip) => (
                  <div key={clip.id} className="rounded-lg border p-3 flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-sm font-medium truncate">{clip._title_override ?? clip.title}</p>
                      <p className="text-xs text-muted-foreground truncate">
                        {clip.streamer || 'Unknown'} · {clip.game || 'Unknown'}
                        {clip._orphan ? ' · reindexed' : ''}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge variant={clip.video_id ? 'secondary' : 'default'}>{clip.video_id ? 'uploaded' : 'ready'}</Badge>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 px-2 text-xs"
                        onClick={() => openOutputClip(clip.id).catch((e) => toast.error(e instanceof Error ? e.message : 'Failed to reveal file'))}
                      >
                        <FolderOpen className="size-3 mr-1" />
                        File
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <WorkflowDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        defaultRecipe={dialogRecipe}
      />
    </div>
  )
}

function StatCard({
  title,
  value,
  subtitle,
  icon: Icon,
  pulse,
}: {
  title: string
  value: number
  subtitle: string
  icon: ComponentType<{ className?: string }>
  pulse?: boolean
}) {
  return (
    <Card>
      <CardHeader className="pb-2 flex flex-row items-center justify-between">
        <CardTitle className="text-xs uppercase tracking-wider text-muted-foreground">{title}</CardTitle>
        <Icon className={`size-4 text-muted-foreground ${pulse ? 'animate-pulse' : ''}`} />
      </CardHeader>
      <CardContent>
        <p className="text-3xl tabular-nums font-semibold">{value}</p>
        <p className="text-xs text-muted-foreground">{subtitle}</p>
      </CardContent>
    </Card>
  )
}
