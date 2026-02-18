import { useEffect, useState, useMemo, useRef, useCallback } from 'react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Checkbox } from '@/components/ui/checkbox'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'
import { Progress } from '@/components/ui/progress'
import {
  ChevronUp, ChevronDown, X, Loader2, Upload, Play,
  ExternalLink, Pencil, Globe, CheckCircle2, Check, Undo2,
} from 'lucide-react'
import { toast } from 'sonner'
import {
  fetchStudioClips, buildCompilation,
  uploadClip, publishVideos, updateClip, thumbnailUrl, videoUrl,
} from '@/lib/api'
import { usePipeline } from '@/hooks/usePipeline'
import { useChannelScope } from '@/hooks/useChannelScope'
import { VideoPreview } from '@/components/VideoPreview'
import { cn } from '@/lib/utils'
import type { ClipMeta } from '@/lib/types'

const DURATION_TIERS = [
  { label: '8 min', hint: 'Mid-roll ads unlock', minutes: 8 },
  { label: '10 min', hint: 'Sweet spot', minutes: 10 },
  { label: '12 min', hint: 'Longer watch time', minutes: 12 },
  { label: '15 min', hint: 'Max engagement', minutes: 15 },
]

type Phase = 'select' | 'order' | 'building'

const STUDIO_FETCH_LIMIT = 1000

function isCompilationClip(clip: ClipMeta): boolean {
  return Boolean((clip.clip_count ?? 0) > 0) || clip.id.startsWith('compilation_')
}

function isCompilationCandidate(clip: ClipMeta): boolean {
  // Compilation requires landscape source + subtitle track.
  return (
    !isCompilationClip(clip)
    && !clip.is_shorts
    && Boolean(clip.processed_path)
    && Boolean(clip._subtitle_path)
  )
}

export function CompilationBuilder() {
  const [tab, setTab] = useState<'ready' | 'build'>('ready')

  return (
    <Tabs value={tab} onValueChange={(v) => setTab(v as 'ready' | 'build')}>
      <TabsList>
        <TabsTrigger value="ready">Ready</TabsTrigger>
        <TabsTrigger value="build">Build New</TabsTrigger>
      </TabsList>
      <TabsContent value="ready" className="mt-4">
        <CompilationReadyList />
      </TabsContent>
      <TabsContent value="build" className="mt-4">
        <CompilationBuildFlow />
      </TabsContent>
    </Tabs>
  )
}

// ─── Ready to Upload ──────────────────────────────────────────────

function CompilationReadyList() {
  const [compilations, setCompilations] = useState<ClipMeta[]>([])
  const [fallbackAllChannels, setFallbackAllChannels] = useState(false)
  const [previewClip, setPreviewClip] = useState<ClipMeta | null>(null)
  const [editingClip, setEditingClip] = useState<ClipMeta | null>(null)
  const [uploadingIds, setUploadingIds] = useState<Set<string>>(new Set())
  const { channel: workspaceChannel } = useChannelScope()

  async function load() {
    try {
      const channelFilter = workspaceChannel !== 'all' ? workspaceChannel : undefined
      const clips = await fetchStudioClips({
        sort: 'recent',
        limit: STUDIO_FETCH_LIMIT,
        channel: channelFilter,
      })
      let comps = clips.filter(isCompilationClip)

      if (comps.length === 0 && workspaceChannel !== 'all') {
        const allClips = await fetchStudioClips({
          sort: 'recent',
          limit: STUDIO_FETCH_LIMIT,
        })
        comps = allClips.filter(isCompilationClip)
        setFallbackAllChannels(comps.length > 0)
      } else {
        setFallbackAllChannels(false)
      }

      setCompilations(comps)
    } catch {
      // ignore fetch errors in list view
    }
  }

  useEffect(() => { load() }, [workspaceChannel])

  async function handleUpload(clip: ClipMeta) {
    setUploadingIds((s) => new Set(s).add(clip.id))
    try {
      const targetChannel = workspaceChannel === 'all' ? null : workspaceChannel
      const result = await uploadClip(clip.id, 'unlisted', targetChannel)
      toast.success(`Uploaded${result.channel ? ` to ${result.channel}` : ''}: ${result.video_id}`)
      load()
    } catch {
      toast.error('Upload failed')
    } finally {
      setUploadingIds((s) => { const n = new Set(s); n.delete(clip.id); return n })
    }
  }

  async function handlePublish(videoId: string) {
    try {
      await publishVideos([videoId])
      toast.success('Published to public')
      load()
    } catch {
      toast.error('Publish failed')
    }
  }

  if (compilations.length === 0) {
    return (
      <div className="rounded-lg border border-dashed p-8 text-center">
        <p className="text-sm text-muted-foreground">No compilations built yet.</p>
        <p className="text-xs text-muted-foreground mt-2">
          Switch to "Build New" to create one, or run: clipper compile
        </p>
      </div>
    )
  }

  return (
    <>
      {fallbackAllChannels && (
        <p className="text-xs text-muted-foreground mb-3">
          No compilations found in this workspace channel. Showing latest compilations from all channels.
        </p>
      )}
      <div className="space-y-3">
        {compilations.map((comp) => {
          const isUploading = uploadingIds.has(comp.id)
          const mins = comp.duration ? (comp.duration / 60).toFixed(1) : '?'
          return (
            <div key={comp.id} className="rounded-md border p-4 space-y-2">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-medium truncate">
                      {comp._title_override ?? comp.title}
                    </h3>
                    {comp.video_id ? (
                      <Badge variant="secondary" className="shrink-0">uploaded</Badge>
                    ) : (
                      <Badge className="bg-primary text-primary-foreground shrink-0">ready</Badge>
                    )}
                    {comp._title_override && (
                      <Badge variant="outline" className="text-[10px] shrink-0">edited</Badge>
                    )}
                  </div>
                  <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground">
                    <span>{comp.game}</span>
                    <span>{comp.clip_count} clips</span>
                    <span>{mins} min</span>
                    {comp.streamer && <span className="truncate max-w-48">{comp.streamer}</span>}
                  </div>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <Button
                    size="icon" variant="ghost"
                    onClick={() => setPreviewClip(comp)}
                    aria-label="Preview"
                  >
                    <Play className="size-4" />
                  </Button>
                  <Button
                    size="icon" variant="ghost"
                    onClick={() => setEditingClip(comp)}
                    aria-label="Edit title & description"
                  >
                    <Pencil className="size-4" />
                  </Button>
                  {!comp.video_id && (
                    <Button
                      size="sm" variant="outline"
                      onClick={() => handleUpload(comp)}
                      disabled={isUploading}
                      title="Upload unlisted"
                    >
                      {isUploading ? (
                        <Loader2 className="size-4 animate-spin mr-1" />
                      ) : (
                        <Upload className="size-4 mr-1" />
                      )}
                      Upload
                    </Button>
                  )}
                  {comp.video_id && (
                    <>
                      <a
                        href={`https://youtube.com/watch?v=${comp.video_id}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center justify-center size-8 rounded-md hover:bg-accent"
                        aria-label="View on YouTube"
                      >
                        <ExternalLink className="size-4 text-primary" />
                      </a>
                      <Button
                        size="sm" variant="outline"
                        onClick={() => handlePublish(comp.video_id!)}
                        title="Publish (make public)"
                      >
                        <Globe className="size-4 mr-1" />
                        Publish
                      </Button>
                    </>
                  )}
                </div>
              </div>
              {comp._description_override && (
                <details className="text-xs text-muted-foreground">
                  <summary className="cursor-pointer hover:text-foreground">Description</summary>
                  <pre className="mt-2 whitespace-pre-wrap font-sans leading-relaxed max-h-40 overflow-y-auto">
                    {comp._description_override}
                  </pre>
                </details>
              )}
            </div>
          )
        })}
      </div>

      <VideoPreview
        clipId={previewClip?.id ?? null}
        title={previewClip?.title ?? ''}
        open={previewClip !== null}
        onOpenChange={(open) => { if (!open) setPreviewClip(null) }}
      />

      {editingClip && (
        <CompEditDialog
          clip={editingClip}
          open={!!editingClip}
          onOpenChange={(open) => { if (!open) setEditingClip(null) }}
          onSaved={() => { setEditingClip(null); load() }}
        />
      )}
    </>
  )
}

function CompEditDialog({
  clip,
  open,
  onOpenChange,
  onSaved,
}: {
  clip: ClipMeta
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved: () => void
}) {
  const [title, setTitle] = useState(clip._title_override ?? clip.title)
  const [description, setDescription] = useState(clip._description_override ?? '')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setTitle(clip._title_override ?? clip.title)
    setDescription(clip._description_override ?? '')
  }, [clip])

  async function handleSave() {
    setSaving(true)
    try {
      await updateClip(clip.id, { title_override: title, description_override: description })
      toast.success('Saved')
      onSaved()
    } catch {
      toast.error('Failed to save')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Edit Compilation</DialogTitle>
          <DialogDescription>{clip.game} — {clip.clip_count} clips</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <label className="text-xs font-medium text-muted-foreground">Title</label>
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={100}
            />
            <p className="text-xs text-muted-foreground mt-1 text-right">{title.length}/100</p>
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={8}
              className="w-full rounded-md border bg-background px-3 py-2 text-sm resize-y"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={handleSave} disabled={saving || title.length === 0}>
            {saving && <Loader2 className="size-4 animate-spin mr-1" />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ─── Clip Review Card ─────────────────────────────────────────────

function scoreColor(score: number): string {
  if (score >= 70) return 'bg-green-500'
  if (score >= 40) return 'bg-yellow-500'
  return 'bg-red-500'
}

function ClipReviewCard({
  clip,
  decision,
  isPlaying,
  onPlay,
  onApprove,
  onReject,
  onUndo,
}: {
  clip: ClipMeta
  decision?: 'approved' | 'rejected'
  isPlaying: boolean
  onPlay: () => void
  onApprove: () => void
  onReject: () => void
  onUndo: () => void
}) {
  const videoRef = useRef<HTMLVideoElement>(null)

  useEffect(() => {
    if (!isPlaying && videoRef.current) {
      videoRef.current.pause()
    }
  }, [isPlaying])

  const score = clip._score ?? 0
  const dur = (clip.duration ?? 0).toFixed(1)

  return (
    <div
      className={cn(
        'rounded-md border overflow-hidden transition-all',
        decision === 'approved' && 'border-l-4 border-l-green-500',
        decision === 'rejected' && 'opacity-40',
      )}
    >
      {/* Thumbnail / Video */}
      <div
        className="relative aspect-video bg-muted cursor-pointer"
        onClick={onPlay}
      >
        {isPlaying ? (
          <video
            ref={videoRef}
            src={videoUrl(clip.id)}
            autoPlay
            controls
            className="w-full h-full object-cover"
          />
        ) : (
          <>
            <img
              src={thumbnailUrl(clip.id)}
              alt={clip.title}
              className="w-full h-full object-cover"
              loading="lazy"
            />
            <div className="absolute inset-0 flex items-center justify-center bg-black/20 opacity-0 hover:opacity-100 transition-opacity">
              <Play className="size-10 text-white drop-shadow-lg" />
            </div>
          </>
        )}
        {/* Score badge */}
        <Badge
          className={cn(
            'absolute top-2 left-2 text-white text-xs font-bold',
            scoreColor(score),
          )}
        >
          {score.toFixed(0)}
        </Badge>
        {/* Duration badge */}
        <Badge
          variant="secondary"
          className="absolute top-2 right-2 text-xs font-mono"
        >
          {dur}s
        </Badge>
      </div>

      {/* Info */}
      <div className="px-3 py-2">
        <p className="text-sm font-medium truncate" title={clip.title}>
          {clip.title}
        </p>
        <p className="text-xs text-muted-foreground truncate">
          {clip.streamer}{clip.game ? ` · ${clip.game}` : ''}
        </p>
      </div>

      {/* Actions */}
      <div className="px-3 pb-3 flex items-center gap-2">
        {decision === 'rejected' ? (
          <Button size="sm" variant="ghost" className="w-full" onClick={onUndo}>
            <Undo2 className="size-4 mr-1" />
            Undo
          </Button>
        ) : decision === 'approved' ? (
          <>
            <Button size="sm" variant="ghost" className="flex-1 text-muted-foreground" onClick={onUndo}>
              <Undo2 className="size-4 mr-1" />
              Undo
            </Button>
            <Badge variant="outline" className="text-green-500 border-green-500">
              <Check className="size-3 mr-1" />
              Approved
            </Badge>
          </>
        ) : (
          <>
            <Button size="sm" variant="ghost" className="flex-1 text-destructive hover:text-destructive" onClick={onReject}>
              <X className="size-4 mr-1" />
              Reject
            </Button>
            <Button size="sm" variant="default" className="flex-1" onClick={onApprove}>
              <Check className="size-4 mr-1" />
              Approve
            </Button>
          </>
        )}
      </div>
    </div>
  )
}

// ─── Build New ────────────────────────────────────────────────────

function CompilationBuildFlow() {
  const [phase, setPhase] = useState<Phase>('select')
  const [available, setAvailable] = useState<ClipMeta[]>([])
  const [decisions, setDecisions] = useState<Record<string, 'approved' | 'rejected'>>({})
  const [hideRejected, setHideRejected] = useState(false)
  const [playingId, setPlayingId] = useState<string | null>(null)
  const [ordered, setOrdered] = useState<ClipMeta[]>([])
  const [title, setTitle] = useState('')
  const [numberingMode, setNumberingMode] = useState<'countdown' | 'ascending'>('countdown')
  const [search, setSearch] = useState('')
  const [gameFilter, setGameFilter] = useState('')
  const [sortBy] = useState<'score' | 'date'>('score')
  const { state } = usePipeline()
  const { channel: workspaceChannel } = useChannelScope()

  useEffect(() => {
    fetchStudioClips({
      sort: 'score',
      limit: STUDIO_FETCH_LIMIT,
      channel: workspaceChannel !== 'all' ? workspaceChannel : undefined,
    })
      .then((clips) => {
        const candidates = clips.filter(isCompilationCandidate)
        candidates.sort((a, b) => (b._score ?? 0) - (a._score ?? 0))
        setAvailable(candidates)
      })
      .catch(() => {})
  }, [workspaceChannel])

  const approve = useCallback((id: string) => {
    setDecisions((prev) => ({ ...prev, [id]: 'approved' }))
  }, [])

  const reject = useCallback((id: string) => {
    setDecisions((prev) => ({ ...prev, [id]: 'rejected' }))
    if (playingId === id) setPlayingId(null)
  }, [playingId])

  const undoDecision = useCallback((id: string) => {
    setDecisions((prev) => {
      const next = { ...prev }
      delete next[id]
      return next
    })
  }, [])

  const approvedIds = useMemo(
    () => Object.entries(decisions).filter(([, v]) => v === 'approved').map(([k]) => k),
    [decisions],
  )

  const rejectedCount = useMemo(
    () => Object.values(decisions).filter((v) => v === 'rejected').length,
    [decisions],
  )

  function approveTier(clipCount: number) {
    const topN = filtered.slice(0, clipCount)
    const next = { ...decisions }
    for (const clip of topN) next[clip.id] = 'approved'
    setDecisions(next)
  }

  function approveAll() {
    const next = { ...decisions }
    for (const clip of filtered) next[clip.id] = 'approved'
    setDecisions(next)
  }

  function resetDecisions() {
    setDecisions({})
  }

  function addToCompilation() {
    const clips = filtered.filter((c) => decisions[c.id] === 'approved')
    const extraIds = approvedIds.filter((id) => !clips.some((c) => c.id === id))
    const extras = available.filter((c) => extraIds.includes(c.id))
    setOrdered([...clips, ...extras])
    setPlayingId(null)
    setPhase('order')
  }

  function moveClip(index: number, dir: 'up' | 'down') {
    const swap = dir === 'up' ? index - 1 : index + 1
    if (swap < 0 || swap >= ordered.length) return
    const next = [...ordered]
    ;[next[index], next[swap]] = [next[swap], next[index]]
    setOrdered(next)
  }

  function removeClip(index: number) {
    setOrdered((prev) => prev.filter((_, i) => i !== index))
  }

  async function handleBuild() {
    if (ordered.length < 2) {
      toast.error('Need at least 2 clips')
      return
    }
    setPhase('building')
    try {
      const targetChannel = workspaceChannel === 'all' ? null : workspaceChannel
      await buildCompilation(
        ordered.map((c) => c.id),
        title || undefined,
        numberingMode === 'countdown',
        targetChannel,
      )
      toast.success('Compilation build started')
    } catch {
      toast.error('Failed to start compilation')
      setPhase('order')
    }
  }

  const games = useMemo(() => {
    const set = new Set<string>()
    for (const c of available) if (c.game) set.add(c.game)
    return Array.from(set).sort()
  }, [available])

  const filtered = useMemo(() => {
    const q = search.toLowerCase()
    let clips = available.filter((c) => {
      if (gameFilter && c.game !== gameFilter) return false
      if (q && !c.title.toLowerCase().includes(q) && !c.streamer.toLowerCase().includes(q)) return false
      return true
    })
    if (sortBy === 'score') {
      clips.sort((a, b) => (b._score ?? 0) - (a._score ?? 0))
    }
    // 'score' is the default from the backend, already sorted
    return clips
  }, [available, search, gameFilter, sortBy])

  // Duration tiers computed from filtered clips
  const tiers = useMemo(() => {
    return DURATION_TIERS.map((tier) => {
      let cumDuration = 0
      let clipCount = 0
      let scoreSum = 0
      for (const clip of filtered) {
        cumDuration += clip.duration
        clipCount++
        scoreSum += clip._score ?? 0
        if (cumDuration / 60 >= tier.minutes) break
      }
      return {
        ...tier,
        clipCount,
        actualMinutes: (cumDuration / 60).toFixed(1),
        avgScore: clipCount > 0 ? (scoreSum / clipCount).toFixed(1) : '0',
        available: cumDuration / 60 >= tier.minutes,
      }
    })
  }, [filtered])

  const totalDuration = ordered.reduce((sum, c) => sum + (c.duration || 0), 0)

  // Building phase
  if (phase === 'building') {
    const step = state?.compile_step
    const progress = state?.compile_progress ?? 0
    const pct = Math.round(progress * 100)
    const done = !step && progress >= 1

    return (
      <div className="space-y-4 py-8 flex flex-col items-center">
        {done ? (
          <>
            <CheckCircle2 className="size-8 text-green-400" />
            <p className="text-lg font-semibold text-green-400">Compilation complete</p>
            <p className="text-xs text-muted-foreground">Check the "Ready" tab to preview, edit, and upload.</p>
            <Button variant="outline" onClick={() => setPhase('select')}>
              Build Another
            </Button>
          </>
        ) : (
          <>
            <Loader2 className="size-8 animate-spin text-primary" />
            <p className="text-sm text-muted-foreground">{step || 'Starting...'}</p>
            <div className="w-64">
              <Progress value={pct} className="h-2 [&>div]:bg-primary" />
            </div>
            <p className="text-xs text-muted-foreground">{pct}%</p>
          </>
        )}
      </div>
    )
  }

  // Order phase
  if (phase === 'order') {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div className="text-sm text-muted-foreground">
            {ordered.length} clips &middot; {Math.round(totalDuration)}s total
            ({(totalDuration / 60).toFixed(1)} min)
          </div>
          <Button variant="outline" size="sm" onClick={() => setPhase('select')}>
            Back
          </Button>
        </div>
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">#</TableHead>
                <TableHead>Title</TableHead>
                <TableHead>Streamer</TableHead>
                <TableHead className="text-right">Duration</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {ordered.map((clip, i) => (
                <TableRow key={clip.id}>
                  <TableCell className="font-mono text-muted-foreground">{i + 1}</TableCell>
                  <TableCell className="max-w-xs truncate">{clip.title}</TableCell>
                  <TableCell className="font-mono text-xs">{clip.streamer}</TableCell>
                  <TableCell className="text-right font-mono">{(clip.duration ?? 0).toFixed(1)}s</TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-1">
                      <Button size="icon" variant="ghost" onClick={() => moveClip(i, 'up')} disabled={i === 0} aria-label="Move up">
                        <ChevronUp className="size-4" />
                      </Button>
                      <Button size="icon" variant="ghost" onClick={() => moveClip(i, 'down')} disabled={i === ordered.length - 1} aria-label="Move down">
                        <ChevronDown className="size-4" />
                      </Button>
                      <Button size="icon" variant="ghost" onClick={() => removeClip(i)} aria-label="Remove clip">
                        <X className="size-4" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <div className="flex items-center gap-3">
          <Input
            placeholder="Compilation title (optional)"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="max-w-sm"
          />
          <select
            value={numberingMode}
            onChange={(e) => setNumberingMode(e.target.value as 'countdown' | 'ascending')}
            className="h-9 rounded-md border bg-background px-3 text-sm text-foreground"
            aria-label="Numbering mode"
            title="Numbering mode for streamer overlay labels"
          >
            <option value="countdown">Numbering: Countdown (#N to #1)</option>
            <option value="ascending">Numbering: Ascending (#1 to #N)</option>
          </select>
          <Button onClick={handleBuild} disabled={ordered.length < 2}>
            Build Compilation
          </Button>
        </div>
      </div>
    )
  }

  // Select phase
  return (
    <div className="space-y-4">
      <div className="rounded-md border border-dashed bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
        Eligible clips: landscape output with subtitle tracks. Compilation render adds streamer + rank overlays.
      </div>
      {/* Duration picker */}
      {filtered.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium uppercase tracking-wider text-muted-foreground">Quick Approve by Duration</h3>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {tiers.map((tier) => (
              <button
                key={tier.label}
                onClick={() => approveTier(tier.clipCount)}
                disabled={!tier.available}
                className="rounded-md border p-3 text-left hover:bg-accent disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                <div className="text-sm font-medium">{tier.label}</div>
                <div className="text-xs text-muted-foreground mt-0.5">{tier.hint}</div>
                <div className="text-xs mt-1.5 space-y-0.5">
                  <div>{tier.clipCount} clips &middot; {tier.actualMinutes} min</div>
                  <div className="text-muted-foreground">Avg score: {tier.avgScore}</div>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Filters + actions */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3">
          <Input
            placeholder="Search title or streamer..."
            aria-label="Search clips"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="max-w-[220px]"
          />
          <select
            value={gameFilter}
            onChange={(e) => setGameFilter(e.target.value)}
            className="h-9 rounded-md border bg-background px-3 text-sm text-foreground"
          >
            <option value="">All games</option>
            {games.map((g) => (
              <option key={g} value={g}>{g}</option>
            ))}
          </select>
          <span className="text-xs text-muted-foreground">
            {approvedIds.length} approved &middot; {rejectedCount} rejected
          </span>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
            <Checkbox
              checked={hideRejected}
              onCheckedChange={(v) => setHideRejected(v === true)}
            />
            Hide rejected
          </label>
          <Button size="sm" variant="ghost" onClick={approveAll}>Approve All</Button>
          {Object.keys(decisions).length > 0 && (
            <Button size="sm" variant="ghost" onClick={resetDecisions}>Reset</Button>
          )}
          <Button size="sm" onClick={addToCompilation} disabled={approvedIds.length === 0}>
            Continue to Order ({approvedIds.length})
          </Button>
        </div>
      </div>

      {/* Clip card grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {filtered
          .filter((c) => !hideRejected || decisions[c.id] !== 'rejected')
          .map((clip) => (
            <ClipReviewCard
              key={clip.id}
              clip={clip}
              decision={decisions[clip.id]}
              isPlaying={playingId === clip.id}
              onPlay={() => setPlayingId(playingId === clip.id ? null : clip.id)}
              onApprove={() => approve(clip.id)}
              onReject={() => reject(clip.id)}
              onUndo={() => undoDecision(clip.id)}
            />
          ))}
        {filtered.length === 0 && (
          <div className="col-span-full text-center py-8 text-muted-foreground">
            {available.length === 0 ? 'No clips available for compilation.' : 'No clips match your search.'}
          </div>
        )}
      </div>
    </div>
  )
}
