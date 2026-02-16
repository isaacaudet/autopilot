import { type ComponentType, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Progress } from '@/components/ui/progress'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Activity,
  Calendar,
  Film,
  ListChecks,
  ListVideo,
  Loader2,
  Play,
  Upload,
  X,
  Zap,
} from 'lucide-react'
import { toast } from 'sonner'
import {
  fetchConfig,
  fetchQueue,
  fetchReleases,
  fetchScore,
  startAutopilot,
} from '@/lib/api'
import type { ClipMeta, ConfigData, Release } from '@/lib/types'
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

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

export function HomePage() {
  const navigate = useNavigate()
  const { state: pipeline } = usePipeline()
  const { channel } = useChannelScope()

  const [pending, setPending] = useState<ClipMeta[]>([])
  const [approved, setApproved] = useState<ClipMeta[]>([])
  const [output, setOutput] = useState<ClipMeta[]>([])
  const [releases, setReleases] = useState<Release[]>([])
  const [, setLoading] = useState(true)
  const [config, setConfig] = useState<ConfigData | null>(null)

  // Shorts workflow state
  const [game, setGame] = useState('')
  const [fetchWindow, setFetchWindow] = useState('24h')
  const [fetchScope, setFetchScope] = useState<'gamewide' | 'configured' | 'selected'>('configured')
  const [selectedStreamers, setSelectedStreamers] = useState<string[]>([])
  const [fetching, setFetching] = useState(false)

  // Compilation dialog
  const [compilationOpen, setCompilationOpen] = useState(false)

  // Autopilot
  const [autopilotCount, setAutopilotCount] = useState(5)
  const [autopilotMinScore, setAutopilotMinScore] = useState(40)
  const [autopilotLoading, setAutopilotLoading] = useState(false)

  const isRunning = pipeline?.running === true
  const activeWorkers = useMemo(
    () =>
      pipeline
        ? Object.values(pipeline.workers).filter(
            ([, step]) => step !== 'done' && step !== 'error',
          ).length
        : 0,
    [pipeline],
  )
  const pipelineProgress = useMemo(() => {
    if (!pipeline || pipeline.total <= 0) return null
    const processed = pipeline.completed + pipeline.failed
    const pct = Math.max(0, Math.min(100, Math.round((processed / pipeline.total) * 100)))
    return { processed, pct }
  }, [pipeline])

  const games = config?.targets?.twitch?.games ?? []
  const configuredStreamers = config?.targets?.twitch?.streamers ?? []
  const readyCount = output.filter((c) => !c.video_id).length
  const scheduledCount = releases.filter((r) => r.status === 'pending').length

  async function load() {
    setLoading(true)
    try {
      const channelKey = channel !== 'all' ? channel : undefined
      const [cfg, pendingClips, approvedClips, outputClips, releaseRows] = await Promise.all([
        fetchConfig(),
        fetchQueue('pending', { sort: 'recent', channel: channelKey, limit: 250 }),
        fetchQueue('pending', { sort: 'recent', channel: channelKey, limit: 250 }),
        fetchQueue('output', { sort: 'recent', channel: channelKey, limit: 50 }),
        fetchReleases(channelKey),
      ])
      setConfig(cfg)
      setPending(pendingClips)
      setApproved(approvedClips.filter((c) => c._score && c._score > 0))
      setOutput(outputClips)
      setReleases(releaseRows)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load().catch(() => {})
  }, [channel])

  async function handleFetchScore() {
    if (!game) return
    setFetching(true)
    try {
      const targetChannel = channel === 'all' ? null : channel
      const streamersToFetch = fetchScope === 'selected' ? selectedStreamers : []
      await fetchScore(game, targetChannel, {
        period: fetchWindow,
        fetchScope,
        streamers: streamersToFetch,
      })
      toast.success('Clips fetched and scored')
      navigate('/review')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Fetch failed')
    } finally {
      setFetching(false)
    }
  }

  async function handleAutopilot() {
    setAutopilotLoading(true)
    try {
      const targetChannel = channel === 'all' ? null : channel
      await startAutopilot({
        count: autopilotCount,
        min_score: autopilotMinScore,
        channel: targetChannel,
      })
      toast.success('Autopilot started')
      navigate('/pipeline')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Autopilot failed to start')
    } finally {
      setAutopilotLoading(false)
    }
  }

  return (
    <div className="space-y-5">
      <div className="space-y-1">
        <h1 className="text-xl font-semibold tracking-tight">Home</h1>
        <p className="text-sm text-muted-foreground">
          Fetch, review, and process clips in one flow.
        </p>
      </div>

      {/* Quick Stats */}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          title="Pending"
          value={pending.length}
          subtitle="clips to review"
          icon={ListVideo}
          onClick={() => pending.length > 0 && navigate('/review')}
        />
        <StatCard
          title="Ready"
          value={readyCount}
          subtitle="output clips"
          icon={Upload}
          onClick={() => readyCount > 0 && navigate('/studio')}
        />
        <StatCard
          title="Scheduled"
          value={scheduledCount}
          subtitle="releases pending"
          icon={Calendar}
          onClick={() => navigate('/schedule')}
        />
        <StatCard
          title="Workers"
          value={activeWorkers}
          subtitle={isRunning ? 'processing' : 'idle'}
          icon={Activity}
          pulse={isRunning}
          onClick={() => isRunning && navigate('/pipeline')}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        {/* Shorts Workflow */}
        <Card className="hover:border-primary/20 transition-colors">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <Play className="size-4 text-primary" />
              Shorts Workflow
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-xs text-muted-foreground">
              Fetch clips, review & approve, then process into shorts.
            </p>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <label className="text-xs font-medium">Game</label>
                <Select value={game} onValueChange={setGame}>
                  <SelectTrigger className="h-9">
                    <SelectValue placeholder="Select game" />
                  </SelectTrigger>
                  <SelectContent>
                    {games.map((g) => (
                      <SelectItem key={g} value={g}>
                        {g}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium">Fetch Window</label>
                <Select value={fetchWindow} onValueChange={setFetchWindow}>
                  <SelectTrigger className="h-9">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="3h">Last 3 hours</SelectItem>
                    <SelectItem value="6h">Last 6 hours</SelectItem>
                    <SelectItem value="12h">Last 12 hours</SelectItem>
                    <SelectItem value="24h">Last 24 hours</SelectItem>
                    <SelectItem value="48h">Last 48 hours</SelectItem>
                    <SelectItem value="7d">Last 7 days</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium">Source Scope</label>
              <Select
                value={fetchScope}
                onValueChange={(v) => setFetchScope(v as 'gamewide' | 'configured' | 'selected')}
              >
                <SelectTrigger className="h-9">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="gamewide">Game-wide (all channels)</SelectItem>
                  <SelectItem value="configured">All configured streamers</SelectItem>
                  <SelectItem value="selected">Selected streamers</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {fetchScope === 'selected' && (
              <div className="space-y-1.5">
                <label className="text-xs font-medium">Streamers</label>
                <div className="flex flex-wrap gap-1.5 min-h-[32px]">
                  {selectedStreamers.map((s) => (
                    <Badge
                      key={s}
                      variant="secondary"
                      className="gap-1 pr-1 cursor-pointer hover:bg-destructive/20"
                      onClick={() => setSelectedStreamers((prev) => prev.filter((x) => x !== s))}
                    >
                      {s}
                      <X className="size-3" />
                    </Badge>
                  ))}
                </div>
                {configuredStreamers.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {configuredStreamers
                      .filter((s) => !selectedStreamers.includes(s))
                      .map((s) => (
                        <button
                          key={s}
                          type="button"
                          className="rounded-md border px-2 py-0.5 text-xs text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
                          onClick={() => setSelectedStreamers((prev) => [...prev, s])}
                        >
                          + {s}
                        </button>
                      ))}
                  </div>
                )}
                <Input
                  placeholder="Or type a streamer name and press Enter..."
                  className="h-8 text-xs"
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      const val = (e.target as HTMLInputElement).value.trim()
                      if (val && !selectedStreamers.includes(val)) {
                        setSelectedStreamers((prev) => [...prev, val])
                      }
                      ;(e.target as HTMLInputElement).value = ''
                      e.preventDefault()
                    }
                  }}
                />
              </div>
            )}

            <Button onClick={handleFetchScore} disabled={!game || fetching || (fetchScope === 'selected' && selectedStreamers.length === 0)} className="w-full">
              {fetching ? (
                <>
                  <Loader2 className="size-4 animate-spin mr-1.5" />
                  Fetching...
                </>
              ) : (
                'Fetch & Score'
              )}
            </Button>

            {pending.length > 0 && (
              <Button
                variant="outline"
                className="w-full"
                onClick={() => navigate('/review')}
              >
                <ListChecks className="size-4 mr-1.5" />
                {pending.length} clips pending — Review & Approve
              </Button>
            )}

            {approved.length > 0 && (
              <Button
                variant="outline"
                className="w-full"
                onClick={() => navigate('/edit')}
              >
                <Film className="size-4 mr-1.5" />
                Process {approved.length} Approved Clips
              </Button>
            )}
          </CardContent>
        </Card>

        {/* Compilation + Autopilot */}
        <div className="space-y-4">
          <Card className="hover:border-primary/20 transition-colors">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <Film className="size-4 text-primary" />
                Compilation
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-xs text-muted-foreground">
                Build a long-form compilation from processed clips.
              </p>
              <Button
                variant="outline"
                onClick={() => {
                  setCompilationOpen(true)
                }}
              >
                Start Compilation
              </Button>
            </CardContent>
          </Card>

          <Card className="hover:border-primary/20 transition-colors">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <Zap className="size-4 text-primary" />
                Autopilot
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-xs text-muted-foreground">
                Hands-off: fetch, score, auto-approve top clips, process & upload.
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
                  {autopilotLoading ? (
                    <Loader2 className="size-4 animate-spin mr-1" />
                  ) : (
                    <Zap className="size-4 mr-1" />
                  )}
                  Run Autopilot
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Pipeline Monitor (compact) */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <Activity className="size-4 text-primary" />
            Pipeline
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
          {pipelineProgress && (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                <span>
                  {pipelineProgress.processed}/{pipeline?.total ?? 0} processed
                </span>
                <span>{pipelineProgress.pct}%</span>
              </div>
              <Progress value={pipelineProgress.pct} className="h-2 [&>div]:bg-primary" />
              <div className="text-[11px] text-muted-foreground">
                {pipeline && pipeline.elapsed > 0
                  ? `${formatTime(pipeline.elapsed)} elapsed`
                  : '0:00 elapsed'}
                {pipeline?.eta != null && pipeline.eta > 0
                  ? ` · ~${formatTime(pipeline.eta)} left`
                  : ''}
              </div>
            </div>
          )}
          {pipeline?.errors?.length ? (
            <div className="rounded-md border border-destructive/25 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              Last error: {pipeline.errors[pipeline.errors.length - 1]}
            </div>
          ) : null}
          <Button variant="outline" size="sm" onClick={() => navigate('/pipeline')}>
            View Pipeline
          </Button>
        </CardContent>
      </Card>

      <WorkflowDialog
        open={compilationOpen}
        onOpenChange={setCompilationOpen}
        defaultRecipe="compilation"
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
  onClick,
}: {
  title: string
  value: number
  subtitle: string
  icon: ComponentType<{ className?: string }>
  pulse?: boolean
  onClick?: () => void
}) {
  return (
    <Card
      className={onClick ? 'cursor-pointer hover:border-primary/20 transition-colors' : ''}
      onClick={onClick}
    >
      <CardHeader className="pb-2 flex flex-row items-center justify-between">
        <CardTitle className="text-xs uppercase tracking-wider text-muted-foreground">
          {title}
        </CardTitle>
        <Icon className={`size-4 text-muted-foreground ${pulse ? 'animate-pulse' : ''}`} />
      </CardHeader>
      <CardContent>
        <p className="text-3xl tabular-nums font-semibold">{value}</p>
        <p className="text-xs text-muted-foreground">{subtitle}</p>
      </CardContent>
    </Card>
  )
}
