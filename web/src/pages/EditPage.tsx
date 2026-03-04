import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Check,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  Loader2,
  Play,
  SlidersHorizontal,
  X,
} from 'lucide-react'
import { toast } from 'sonner'
import {
  approveProcess,
  batchReview,
  fetchQueue,
  thumbnailUrl,
  type LayoutProfile,
} from '@/lib/api'
import type { ClipMeta } from '@/lib/types'
import { useChannelScope } from '@/hooks/useChannelScope'
import { CropProfilesDialog } from '@/components/CropProfilesDialog'

/* ------------------------------------------------------------------ */
/*  Per-clip title / hook / trim overrides (crop lives in dialog)       */
/* ------------------------------------------------------------------ */

interface TitleOverrides {
  title: string
  hookText: string
}

interface TrimOverrides {
  trimStart: number
  trimEnd: number
}

function defaultTitleOverrides(clip: ClipMeta): TitleOverrides {
  return {
    // Only pre-fill with an explicit override or LLM variant — never the raw Twitch title.
    // An empty string here means "auto-generate at upload time" via _build_title.
    title: clip._title_override ?? clip._analysis?.title_variants?.[0] ?? '',
    hookText: clip._hook_text_override ?? clip._analysis?.hook_text ?? '',
  }
}

function defaultTrimOverrides(clip: ClipMeta): TrimOverrides {
  const rawStart = Number(clip._trim_start ?? 0)
  const rawEnd = Number(clip._trim_end ?? 0)
  const duration = Math.max(0, Number(clip.duration || 0))
  const trimStart = Math.max(0, Number.isFinite(rawStart) ? rawStart : 0)
  const maxEnd = Math.max(0, duration - trimStart - 0.2)
  const trimEnd = Math.max(0, Math.min(maxEnd, Number.isFinite(rawEnd) ? rawEnd : 0))
  return {
    trimStart: Math.round(trimStart * 100) / 100,
    trimEnd: Math.round(trimEnd * 100) / 100,
  }
}

function clipThumb(clip: ClipMeta): string {
  return clip.thumbnail_url || thumbnailUrl(clip.id)
}

function formatDuration(seconds: number): string {
  const total = Math.max(0, Number.isFinite(seconds) ? seconds : 0)
  const mins = Math.floor(total / 60)
  const secs = Math.floor(total % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

/* ------------------------------------------------------------------ */
/*  EditPage                                                           */
/* ------------------------------------------------------------------ */

export function EditPage() {
  const navigate = useNavigate()
  const { channel } = useChannelScope()

  const [clips, setClips] = useState<ClipMeta[]>([])
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [currentIndex, setCurrentIndex] = useState(0)

  // Title / hook per clip
  const [titleMap, setTitleMap] = useState<Record<string, TitleOverrides>>({})
  const [trimMap, setTrimMap] = useState<Record<string, TrimOverrides>>({})

  // Layout mode
  const [shortsLayout, setShortsLayout] = useState<'blur' | 'fill'>(() => {
    try {
      const v = localStorage.getItem('clipper.shortsLayout') || 'blur'
      return v === 'fill' ? 'fill' : 'blur'
    } catch {
      return 'blur'
    }
  })

  // Crop dialog — the full editing experience from CropProfilesDialog
  const [cropDialogOpen, setCropDialogOpen] = useState(false)
  const [confirmedClipIds, setConfirmedClipIds] = useState<string[]>([])
  const [clipOverrides, setClipOverrides] = useState<Record<string, LayoutProfile>>({})

  // ── Load ──
  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchQueue('approved', { sort: '', limit: 500 })
      setClips(data)
    } catch {
      toast.error('Failed to load clips')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  // Init title overrides when clips arrive
  useEffect(() => {
    if (clips.length === 0) return
    setTitleMap((prev) => {
      let changed = false
      const next = { ...prev }
      for (const clip of clips) {
        if (!next[clip.id]) { next[clip.id] = defaultTitleOverrides(clip); changed = true }
      }
      return changed ? next : prev
    })
  }, [clips])

  useEffect(() => {
    if (clips.length === 0) return
    setTrimMap((prev) => {
      let changed = false
      const next = { ...prev }
      for (const clip of clips) {
        if (!next[clip.id]) {
          next[clip.id] = defaultTrimOverrides(clip)
          changed = true
        }
      }
      return changed ? next : prev
    })
  }, [clips])

  // ── Derived ──
  const currentClip = clips[currentIndex] ?? null
  const titleOv = currentClip ? (titleMap[currentClip.id] ?? defaultTitleOverrides(currentClip)) : null
  const trimOv = currentClip ? (trimMap[currentClip.id] ?? defaultTrimOverrides(currentClip)) : null
  const trimmedDuration = currentClip && trimOv
    ? Math.max(0, Number(currentClip.duration || 0) - trimOv.trimStart - trimOv.trimEnd)
    : 0
  const confirmedSet = useMemo(() => new Set(confirmedClipIds), [confirmedClipIds])
  const clipIds = useMemo(() => clips.map((c) => c.id), [clips])
  const isFill = shortsLayout === 'fill'

  // ── Filmstrip scroll ──
  const stripRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = stripRef.current?.children[currentIndex] as HTMLElement | undefined
    el?.scrollIntoView({ inline: 'center', behavior: 'smooth', block: 'nearest' })
  }, [currentIndex])

  // ── Handlers ──
  function updateTitle(clipId: string, key: keyof TitleOverrides, value: string) {
    setTitleMap((prev) => ({ ...prev, [clipId]: { ...prev[clipId], [key]: value } }))
  }

  function updateTrim(clipId: string, key: keyof TrimOverrides, rawValue: number) {
    setTrimMap((prev) => {
      const clip = clips.find((c) => c.id === clipId)
      if (!clip) return prev
      const duration = Math.max(0, Number(clip.duration || 0))
      const current = prev[clipId] ?? defaultTrimOverrides(clip)
      let trimStart = Math.max(0, Number.isFinite(current.trimStart) ? current.trimStart : 0)
      let trimEnd = Math.max(0, Number.isFinite(current.trimEnd) ? current.trimEnd : 0)
      const nextRaw = Math.max(0, Number.isFinite(rawValue) ? rawValue : 0)

      if (key === 'trimStart') {
        trimStart = Math.min(nextRaw, Math.max(0, duration - trimEnd - 0.2))
      } else {
        trimEnd = Math.min(nextRaw, Math.max(0, duration - trimStart - 0.2))
      }

      if (trimStart + trimEnd > Math.max(0, duration - 0.2)) {
        if (key === 'trimStart') {
          trimEnd = Math.max(0, duration - trimStart - 0.2)
        } else {
          trimStart = Math.max(0, duration - trimEnd - 0.2)
        }
      }

      const next: TrimOverrides = {
        trimStart: Math.round(trimStart * 100) / 100,
        trimEnd: Math.round(trimEnd * 100) / 100,
      }
      if (current.trimStart === next.trimStart && current.trimEnd === next.trimEnd) return prev
      return { ...prev, [clipId]: next }
    })
  }

  function go(idx: number) {
    setCurrentIndex(Math.max(0, Math.min(clips.length - 1, idx)))
  }

  function handleLayoutChange(v: string) {
    const layout = v as 'blur' | 'fill'
    setShortsLayout(layout)
    try { localStorage.setItem('clipper.shortsLayout', v) } catch { /* */ }
    if (layout === 'fill' && clips.length > 0) setCropDialogOpen(true)
  }

  async function removeClip(clipId: string) {
    const removedIndex = clips.findIndex(c => c.id === clipId)
    if (removedIndex === -1) return
    try {
      await batchReview([clipId], 'skip')
    } catch {
      toast.error('Failed to remove clip')
      return
    }
    setClips(prev => prev.filter(c => c.id !== clipId))
    setCurrentIndex(prev => {
      const newLen = clips.length - 1
      if (newLen === 0) return 0
      if (removedIndex < prev) return prev - 1
      if (removedIndex === prev && prev >= newLen) return newLen - 1
      return prev
    })
    setTitleMap(prev => { const n = { ...prev }; delete n[clipId]; return n })
    setTrimMap(prev => { const n = { ...prev }; delete n[clipId]; return n })
    setClipOverrides(prev => { const n = { ...prev }; delete n[clipId]; return n })
    setConfirmedClipIds(prev => prev.filter(id => id !== clipId))
    toast.success('Clip skipped')
  }

  async function handleProcessAll() {
    if (clips.length === 0) return
    setSubmitting(true)
    try {
      const clipIds = clips.map((c) => c.id)
      const targetChannel = channel === 'all' ? null : channel
      const mergedOverrides: Record<string, Record<string, unknown>> = {}
      for (const clip of clips) {
        const cropOv = clipOverrides[clip.id]
        const tOv = titleMap[clip.id]
        const trimOv = trimMap[clip.id] ?? defaultTrimOverrides(clip)
        const titleOverride = String(tOv?.title ?? '').trim()
        const hookOverride = String(tOv?.hookText ?? '').trim()
        mergedOverrides[clip.id] = {
          ...(cropOv ?? {}),
          _title_override: titleOverride || undefined,
          _hook_text_override: hookOverride || undefined,
          _trim_start: Math.max(0, Number(trimOv.trimStart || 0)),
          _trim_end: Math.max(0, Number(trimOv.trimEnd || 0)),
        }
      }
      await approveProcess(clipIds, 'shorts', targetChannel, shortsLayout, mergedOverrides)
      toast.success('Processing started')
      navigate('/pipeline')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to start processing')
    } finally {
      setSubmitting(false)
    }
  }

  // ── Loading / Empty ──
  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (clips.length === 0) {
    return (
      <div className="py-24 text-center space-y-4">
        <div className="mx-auto size-16 rounded-2xl border border-dashed flex items-center justify-center text-muted-foreground">
          <Play className="size-6" />
        </div>
        <p className="text-muted-foreground text-sm">No approved clips to edit.</p>
        <Button variant="outline" onClick={() => navigate('/review')}>
          Go to Review
        </Button>
      </div>
    )
  }

  return (
    <div className="space-y-5">
      {/* ── Header ── */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">
            Edit
            <span className="ml-2 text-sm font-normal text-muted-foreground">
              {clips.length} clip{clips.length !== 1 ? 's' : ''}
            </span>
          </h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Set titles & hooks, then review crop layout before processing.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Select value={shortsLayout} onValueChange={handleLayoutChange}>
            <SelectTrigger className="h-8 w-[140px] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="blur">Classic (blur)</SelectItem>
              <SelectItem value="fill">Fill portrait</SelectItem>
            </SelectContent>
          </Select>

          {isFill && (
            <Button size="sm" variant="outline" onClick={() => setCropDialogOpen(true)} className="text-xs gap-1.5">
              <SlidersHorizontal className="size-3.5" />
              Crops
              {confirmedClipIds.length > 0 && (
                <Badge variant="secondary" className="ml-1 h-4 px-1.5 text-[10px]">
                  {confirmedClipIds.length}/{clips.length}
                </Badge>
              )}
            </Button>
          )}
        </div>
      </div>

      {/* ── Filmstrip ── */}
      <div className="relative">
        <div
          ref={stripRef}
          className="flex gap-1.5 overflow-x-auto pb-1.5 scrollbar-thin"
          style={{ scrollbarColor: 'rgba(255,255,255,0.08) transparent' }}
        >
          {clips.map((clip, i) => (
            <div key={clip.id} className="relative flex-none group/thumb">
              <button
                onClick={() => go(i)}
                className={`relative rounded-md overflow-hidden border-2 transition-all duration-150 ${
                  i === currentIndex
                    ? 'border-primary ring-1 ring-primary/30 scale-[1.02]'
                    : 'border-transparent opacity-60 hover:opacity-90'
                }`}
                style={{ width: 96, height: 54 }}
              >
                <img
                  src={clipThumb(clip)}
                  alt=""
                  className="absolute inset-0 w-full h-full object-cover"
                  loading="lazy"
                />
                {/* Index pill */}
                <span className="absolute bottom-0.5 left-0.5 rounded bg-black/70 px-1 text-[9px] font-mono text-white/80 leading-tight">
                  {i + 1}
                </span>
                {/* Crop confirmed check */}
                {isFill && confirmedSet.has(clip.id) && (
                  <span className="absolute top-0.5 right-0.5 rounded-full bg-green-500 p-[2px]">
                    <Check className="size-2 text-white" />
                  </span>
                )}
              </button>
              {/* Remove clip */}
              <button
                onClick={(e) => { e.stopPropagation(); removeClip(clip.id) }}
                className="absolute top-0.5 right-0.5 z-10 rounded-full bg-red-500/80 p-0.5 opacity-0 group-hover/thumb:opacity-100 transition-opacity"
              >
                <X className="size-2.5 text-white" />
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* ── Main Panel ── */}
      {currentClip && titleOv && trimOv && (
        <div className="grid gap-5 lg:grid-cols-[1fr_380px]">
          {/* Left — Large thumbnail */}
          <div className="space-y-3">
            <a
              href={currentClip.url}
              target="_blank"
              rel="noopener noreferrer"
              className="relative block aspect-video rounded-xl overflow-hidden bg-black border group"
            >
              <img
                src={clipThumb(currentClip)}
                alt={currentClip.title}
                className="absolute inset-0 w-full h-full object-cover"
              />

              {/* Hover overlay */}
              <div className="absolute inset-0 flex items-center justify-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity bg-black/40">
                <ExternalLink className="size-5 text-white/90" />
                <span className="text-xs text-white/80">Watch on Twitch</span>
              </div>

              {/* Badges */}
              <div className="absolute bottom-2.5 left-2.5 flex items-center gap-1.5">
                <Badge variant="outline" className="bg-black/75 text-white border-0 text-[11px] backdrop-blur-sm">
                  {currentClip.streamer}
                </Badge>
                <Badge variant="outline" className="bg-black/75 text-white border-0 text-[11px] backdrop-blur-sm">
                  {currentClip.game}
                </Badge>
              </div>

              {currentClip._score != null && (
                <div className="absolute top-2.5 right-2.5">
                  <Badge
                    variant="outline"
                    className={`font-mono text-[11px] backdrop-blur-sm border-0 ${
                      currentClip._score >= 60
                        ? 'bg-green-500/20 text-green-300'
                        : currentClip._score >= 40
                          ? 'bg-yellow-500/20 text-yellow-300'
                          : 'bg-red-500/20 text-red-300'
                    }`}
                  >
                    {currentClip._score.toFixed(0)} pts
                  </Badge>
                </div>
              )}

              {isFill && confirmedSet.has(currentClip.id) && (
                <div className="absolute top-2.5 left-2.5">
                  <Badge className="bg-green-500/90 text-white border-0 text-[11px] gap-1">
                    <Check className="size-3" />
                    Crop set
                  </Badge>
                </div>
              )}
            </a>

            {/* Nav row under thumbnail */}
            <div className="flex items-center justify-between">
              <Button variant="ghost" size="sm" onClick={() => go(currentIndex - 1)} disabled={currentIndex === 0} className="gap-1 text-xs">
                <ChevronLeft className="size-3.5" />
                Prev
              </Button>
              <span className="text-xs text-muted-foreground tabular-nums">
                {currentIndex + 1} / {clips.length}
              </span>
              <Button variant="ghost" size="sm" onClick={() => go(currentIndex + 1)} disabled={currentIndex === clips.length - 1} className="gap-1 text-xs">
                Next
                <ChevronRight className="size-3.5" />
              </Button>
            </div>
          </div>

          {/* Right — Controls */}
          <div className="space-y-4">
            {/* Short Title */}
            <div className="rounded-xl border bg-card/50 p-4 space-y-3">
              <div className="flex items-center justify-between">
                <div className="text-[11px] font-medium uppercase tracking-widest text-muted-foreground">
                  Short Title
                </div>
                {currentClip.title && titleOv.title !== currentClip.title && (
                  <div className="text-[10px] text-muted-foreground truncate max-w-[200px]" title={currentClip.title}>
                    Original: {currentClip.title}
                  </div>
                )}
              </div>
              <Input
                value={titleOv.title}
                onChange={(e) => updateTitle(currentClip.id, 'title', e.target.value)}
                placeholder={currentClip._analysis?.title_variants?.[0] ?? currentClip.title ?? 'Title'}
                className="text-sm"
              />
              {currentClip._analysis?.title_variants && currentClip._analysis.title_variants.length > 1 && (
                <div className="flex flex-wrap gap-1">
                  {currentClip._analysis.title_variants.slice(0, 4).map((v, i) => (
                    <button
                      key={i}
                      onClick={() => updateTitle(currentClip.id, 'title', v)}
                      className={`rounded-md border px-2 py-1 text-[11px] transition-colors ${
                        titleOv.title === v
                          ? 'border-primary/50 bg-primary/10 text-primary'
                          : 'border-transparent bg-muted/50 text-muted-foreground hover:bg-muted hover:text-foreground'
                      }`}
                    >
                      {v}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* In-Clip Title */}
            <div className="rounded-xl border bg-card/50 p-4 space-y-3">
              <div className="text-[11px] font-medium uppercase tracking-widest text-muted-foreground">
                In-Clip Title
              </div>
              <Input
                value={titleOv.hookText}
                onChange={(e) => updateTitle(currentClip.id, 'hookText', e.target.value)}
                placeholder="Overlay text for first 2 seconds"
                className="text-sm"
              />
            </div>

            {/* Trim */}
            <div className="rounded-xl border bg-card/50 p-4 space-y-3">
              <div className="flex items-center justify-between">
                <div className="text-[11px] font-medium uppercase tracking-widest text-muted-foreground">
                  Trim
                </div>
                <div className="text-[11px] text-muted-foreground">
                  Result: {formatDuration(trimmedDuration)}
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1">
                  <div className="text-[11px] text-muted-foreground">Start cut (s)</div>
                  <Input
                    type="number"
                    step={0.1}
                    min={0}
                    max={Math.max(0, Number(currentClip.duration || 0) - trimOv.trimEnd - 0.2)}
                    value={trimOv.trimStart}
                    onChange={(e) => updateTrim(currentClip.id, 'trimStart', Number.parseFloat(e.target.value))}
                    className="text-sm"
                  />
                </div>
                <div className="space-y-1">
                  <div className="text-[11px] text-muted-foreground">End cut (s)</div>
                  <Input
                    type="number"
                    step={0.1}
                    min={0}
                    max={Math.max(0, Number(currentClip.duration || 0) - trimOv.trimStart - 0.2)}
                    value={trimOv.trimEnd}
                    onChange={(e) => updateTrim(currentClip.id, 'trimEnd', Number.parseFloat(e.target.value))}
                    className="text-sm"
                  />
                </div>
              </div>
            </div>

            {/* Clip info */}
            <div className="rounded-xl border bg-card/50 p-4 space-y-2">
              <div className="text-[11px] font-medium uppercase tracking-widest text-muted-foreground">
                Info
              </div>
              <div className="grid grid-cols-2 gap-y-1.5 text-xs">
                <span className="text-muted-foreground">Views</span>
                <span className="text-right tabular-nums">
                  {currentClip.view_count > 0 ? currentClip.view_count.toLocaleString() : '—'}
                </span>
                <span className="text-muted-foreground">Duration</span>
                <span className="text-right tabular-nums">
                  {formatDuration(Number(currentClip.duration || 0))}
                  {(trimOv.trimStart > 0 || trimOv.trimEnd > 0) ? ` → ${formatDuration(trimmedDuration)}` : ''}
                </span>
                {currentClip._analysis?.category && (
                  <>
                    <span className="text-muted-foreground">Category</span>
                    <span className="text-right">{currentClip._analysis.category}</span>
                  </>
                )}
              </div>
            </div>

            {/* Skip clip */}
            <Button
              variant="ghost"
              className="w-full text-destructive hover:text-destructive hover:bg-destructive/10 gap-1.5"
              onClick={() => removeClip(currentClip.id)}
            >
              <X className="size-4" />
              Skip Clip
            </Button>

            {/* Crop action for fill */}
            {isFill && (
              <Button
                variant={confirmedSet.has(currentClip.id) ? 'outline' : 'default'}
                className="w-full gap-1.5"
                onClick={() => setCropDialogOpen(true)}
              >
                <SlidersHorizontal className="size-4" />
                {confirmedSet.has(currentClip.id) ? 'Edit Crop & Preview' : 'Set Crop & Preview'}
              </Button>
            )}

            {/* Next clip shortcut */}
            {currentIndex < clips.length - 1 && (
              <Button
                variant="outline"
                className="w-full gap-1.5"
                onClick={() => go(currentIndex + 1)}
              >
                Next Clip
                <ChevronRight className="size-4" />
              </Button>
            )}
          </div>
        </div>
      )}

      {/* ── Process Bar ── */}
      <div className="sticky bottom-4 z-10">
        <div className="flex items-center justify-between rounded-xl border bg-background/95 backdrop-blur-md px-5 py-3 shadow-[0_-8px_30px_rgba(0,0,0,0.35)]">
          <div className="text-sm">
            <span className="font-medium">{clips.length}</span>
            <span className="text-muted-foreground"> clip{clips.length !== 1 ? 's' : ''} ready</span>
            {isFill && confirmedClipIds.length > 0 && (
              <span className="text-muted-foreground">
                {' · '}
                <span className="text-green-400">{confirmedClipIds.length}</span> crop{confirmedClipIds.length !== 1 ? 's' : ''} confirmed
              </span>
            )}
          </div>
          <Button onClick={handleProcessAll} disabled={submitting} className="gap-1.5">
            {submitting ? (
              <>
                <Loader2 className="size-4 animate-spin" />
                Starting...
              </>
            ) : (
              <>
                <Play className="size-4" />
                Process All
              </>
            )}
          </Button>
        </div>
      </div>

      {/* ── Crop dialog (full editing experience) ── */}
      <CropProfilesDialog
        open={cropDialogOpen}
        onOpenChange={setCropDialogOpen}
        clips={clips}
        initialStreamer={currentClip?.streamer ?? null}
        reviewRequiredClipIds={clipIds}
        confirmedClipIds={confirmedClipIds}
        onConfirmedClipIdsChange={setConfirmedClipIds}
        clipOverrides={clipOverrides}
        onClipOverridesChange={setClipOverrides}
      />
    </div>
  )
}
