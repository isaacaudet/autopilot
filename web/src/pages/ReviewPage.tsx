import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Checkbox } from '@/components/ui/checkbox'
import { Check, ChevronRight, ExternalLink, Loader2, SkipForward, X } from 'lucide-react'
import { toast } from 'sonner'
import { approveProcess, batchReview, fetchQueue, thumbnailUrl } from '@/lib/api'
import type { ClipMeta } from '@/lib/types'
import { useChannelScope } from '@/hooks/useChannelScope'

type SortKey = 'score' | 'views' | 'recent'
type ReviewTarget = 'shorts' | 'compilation'
const COMPILATION_TARGET_MINUTES = [8, 10, 12, 15] as const
type CompilationTargetMinutes = (typeof COMPILATION_TARGET_MINUTES)[number]

function scoreBadgeColor(score: number | undefined): string {
  if (score == null) return 'bg-muted text-muted-foreground'
  if (score >= 60) return 'bg-green-500/15 text-green-400 border-green-500/25'
  if (score >= 40) return 'bg-yellow-500/15 text-yellow-400 border-yellow-500/25'
  return 'bg-red-500/15 text-red-400 border-red-500/25'
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

function pickCompilationIds(clips: ClipMeta[], targetMinutes: number): string[] {
  if (clips.length === 0) return []
  const targetSeconds = Math.max(1, targetMinutes) * 60
  const ids: string[] = []
  let runtime = 0

  for (const clip of clips) {
    ids.push(clip.id)
    runtime += clip.duration || 0
    if (runtime >= targetSeconds && ids.length >= 2) break
  }

  if (ids.length < 2 && clips.length >= 2) {
    return [clips[0].id, clips[1].id]
  }
  return ids
}

export function ReviewPage() {
  const navigate = useNavigate()
  const { channel: workspaceChannel } = useChannelScope()
  const [clips, setClips] = useState<ClipMeta[]>([])
  const [loading, setLoading] = useState(true)
  const [sort, setSort] = useState<SortKey>('score')
  const [gameFilter, setGameFilter] = useState('__all__')
  const [target, setTarget] = useState<ReviewTarget>(() => {
    try {
      const stored = localStorage.getItem('clipper.reviewTarget')
      return stored === 'compilation' ? 'compilation' : 'shorts'
    } catch {
      return 'shorts'
    }
  })
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [actionLoading, setActionLoading] = useState(false)
  const [processing, setProcessing] = useState(false)
  const [approvedCount, setApprovedCount] = useState(0)
  const [skippedCount, setSkippedCount] = useState(0)
  const [compilationTargetMinutes, setCompilationTargetMinutes] = useState<CompilationTargetMinutes>(() => {
    try {
      const stored = Number(localStorage.getItem('clipper.compilationTargetMinutes') || 10)
      return COMPILATION_TARGET_MINUTES.includes(stored as CompilationTargetMinutes)
        ? (stored as CompilationTargetMinutes)
        : 10
    } catch {
      return 10
    }
  })
  const [compilationSelectionDirty, setCompilationSelectionDirty] = useState(false)

  useEffect(() => {
    try {
      localStorage.setItem('clipper.reviewTarget', target)
    } catch {
      // ignore storage failures
    }
  }, [target])

  useEffect(() => {
    setSelectedIds(new Set())
    if (target === 'compilation') setCompilationSelectionDirty(false)
  }, [target])

  useEffect(() => {
    try {
      localStorage.setItem('clipper.compilationTargetMinutes', String(compilationTargetMinutes))
    } catch {
      // ignore storage failures
    }
  }, [compilationTargetMinutes])

  // Pending clips don't have a channel assigned yet — don't filter by workspace channel.
  const load = useCallback(async () => {
    setLoading(true)
    setClips([])
    setSelectedIds(new Set())
    setApprovedCount(0)
    setSkippedCount(0)
    setCompilationSelectionDirty(false)
    setGameFilter('__all__')
    try {
      const data = await fetchQueue('pending', {
        sort: sort === 'score' ? '' : sort === 'views' ? 'views' : 'recent',
        limit: 500,
      })
      setClips(data)
    } catch {
      toast.error('Failed to load clips')
    } finally {
      setLoading(false)
    }
  }, [sort])

  useEffect(() => {
    load()
  }, [load])

  const games = useMemo(() => {
    const set = new Set<string>()
    for (const c of clips) {
      if (c.game) set.add(c.game)
    }
    return Array.from(set).sort()
  }, [clips])

  const filteredClips = useMemo(() => {
    let result = clips
    if (gameFilter && gameFilter !== '__all__') {
      result = result.filter((c) => c.game === gameFilter)
    }
    if (sort === 'score') {
      result = [...result].sort((a, b) => (b._score ?? 0) - (a._score ?? 0))
    }
    return result
  }, [clips, gameFilter, sort])

  const remaining = filteredClips.length
  const selectedClipList = useMemo(
    () => clips.filter((c) => selectedIds.has(c.id)),
    [clips, selectedIds],
  )
  const selectedDurationSeconds = useMemo(
    () => selectedClipList.reduce((sum, c) => sum + (c.duration || 0), 0),
    [selectedClipList],
  )
  const allFilteredSelected = useMemo(
    () => filteredClips.length > 0 && filteredClips.every((c) => selectedIds.has(c.id)),
    [filteredClips, selectedIds],
  )

  useEffect(() => {
    if (target !== 'compilation') return
    if (compilationSelectionDirty) return
    const ids = pickCompilationIds(filteredClips, compilationTargetMinutes)
    setSelectedIds(new Set(ids))
  }, [target, filteredClips, compilationTargetMinutes, compilationSelectionDirty])

  async function handleAction(clipIds: string[], action: 'approve' | 'skip') {
    setActionLoading(true)
    try {
      await batchReview(clipIds, action)
      if (action === 'approve') {
        setApprovedCount((c) => c + clipIds.length)
      } else {
        setSkippedCount((c) => c + clipIds.length)
      }
      setClips((prev) => prev.filter((c) => !clipIds.includes(c.id)))
      setSelectedIds((prev) => {
        const next = new Set(prev)
        for (const id of clipIds) next.delete(id)
        return next
      })
    } catch {
      toast.error('Action failed')
    } finally {
      setActionLoading(false)
    }
  }

  function handleApproveTopN(n: number) {
    const topIds = filteredClips.slice(0, n).map((c) => c.id)
    handleAction(topIds, 'approve')
  }

  function handleSkipRemaining() {
    const ids = filteredClips.map((c) => c.id)
    handleAction(ids, 'skip')
  }

  function handleBatchAction(action: 'approve' | 'skip') {
    handleAction(Array.from(selectedIds), action)
  }

  async function handleProcessCompilation() {
    const selected = Array.from(selectedIds).filter((id) => clips.some((c) => c.id === id))
    if (selected.length < 2) {
      toast.error('Select at least 2 clips for compilation processing')
      return
    }
    setProcessing(true)
    try {
      const channel = workspaceChannel === 'all' ? null : workspaceChannel
      await approveProcess(selected, 'compilation', channel)
      toast.success('Compilation processing started')
      navigate('/pipeline')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to start compilation processing')
    } finally {
      setProcessing(false)
    }
  }

  function toggleSelect(id: string) {
    if (target === 'compilation') setCompilationSelectionDirty(true)
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleSelectAll() {
    if (target === 'compilation') setCompilationSelectionDirty(true)
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (allFilteredSelected) {
        for (const clip of filteredClips) next.delete(clip.id)
      } else {
        for (const clip of filteredClips) next.add(clip.id)
      }
      return next
    })
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold tracking-tight">Review</h1>
          <p className="text-sm text-muted-foreground">
            {target === 'compilation'
              ? 'Check clips for compilation, tune target runtime, then process.'
              : 'Approve or skip pending clips for Shorts.'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => load()} disabled={loading}>
            {loading ? <Loader2 className="size-4 animate-spin" /> : 'Refresh'}
          </Button>
        </div>
      </div>

      {/* Progress / runtime */}
      {target === 'compilation' ? (
        <div className="rounded-md border bg-muted/15 px-3 py-2 text-sm">
          <span className="font-medium">Runtime</span>
          {' · '}
          <span>{formatDuration(selectedDurationSeconds)}</span>
          {' / '}
          <span>{formatDuration(compilationTargetMinutes * 60)} target</span>
          {' · '}
          <span>{selectedIds.size} checked</span>
          {' · '}
          <span>{remaining} available</span>
        </div>
      ) : (
        <div className="text-sm text-muted-foreground">
          <span className="text-green-400 font-medium">{approvedCount} approved</span>
          {' · '}
          <span>{skippedCount} skipped</span>
          {' · '}
          <span>{remaining} remaining</span>
        </div>
      )}

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <Select value={target} onValueChange={(v) => setTarget(v as ReviewTarget)}>
          <SelectTrigger className="h-8 w-48">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="shorts">Target: Shorts</SelectItem>
            <SelectItem value="compilation">Target: Compilation</SelectItem>
          </SelectContent>
        </Select>

        <Select value={gameFilter} onValueChange={setGameFilter}>
          <SelectTrigger className="h-8 w-40">
            <SelectValue placeholder="All games" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">All games</SelectItem>
            {games.map((g) => (
              <SelectItem key={g} value={g}>
                {g}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={sort} onValueChange={(v) => setSort(v as SortKey)}>
          <SelectTrigger className="h-8 w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="score">Sort by score</SelectItem>
            <SelectItem value="views">Sort by views</SelectItem>
            <SelectItem value="recent">Sort by recent</SelectItem>
          </SelectContent>
        </Select>

        {target === 'compilation' && (
          <Select
            value={String(compilationTargetMinutes)}
            onValueChange={(v) => {
              const next = Number(v) as CompilationTargetMinutes
              setCompilationTargetMinutes(next)
              setCompilationSelectionDirty(false)
            }}
          >
            <SelectTrigger className="h-8 w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {COMPILATION_TARGET_MINUTES.map((mins) => (
                <SelectItem key={mins} value={String(mins)}>
                  Target: {mins} min
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}

        <div className="ml-auto flex items-center gap-2">
          {target === 'compilation' && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setCompilationSelectionDirty(false)}
              disabled={filteredClips.length === 0}
            >
              Auto-select
            </Button>
          )}
          {target === 'compilation' && (
            <Button
              size="sm"
              onClick={handleProcessCompilation}
              disabled={processing || selectedIds.size < 2}
            >
              {processing ? <Loader2 className="size-3.5 animate-spin mr-1" /> : null}
              Process checked ({selectedIds.size})
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={toggleSelectAll}>
            {allFilteredSelected ? 'Uncheck all' : 'Check all'}
          </Button>
          {target === 'shorts' && (
            <>
              <Button size="sm" variant="outline" onClick={() => handleApproveTopN(5)} disabled={filteredClips.length === 0}>
                Approve top 5
              </Button>
              <Button size="sm" variant="outline" onClick={() => handleApproveTopN(10)} disabled={filteredClips.length < 10}>
                Approve top 10
              </Button>
              <Button size="sm" variant="outline" onClick={() => handleApproveTopN(20)} disabled={filteredClips.length < 20}>
                Approve top 20
              </Button>
              <Button size="sm" variant="ghost" onClick={handleSkipRemaining} disabled={filteredClips.length === 0}>
                Skip remaining
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Selection bar */}
      {target === 'shorts' && selectedIds.size > 0 && (
        <div className="sticky bottom-4 z-10 flex items-center gap-3 rounded-lg border bg-background/95 backdrop-blur px-4 py-3 shadow-lg">
          <span className="text-sm font-medium">{selectedIds.size} selected</span>
          <Button
            size="sm"
            onClick={() => handleBatchAction('approve')}
            disabled={actionLoading}
          >
            <Check className="size-3.5 mr-1" />
            Approve
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => handleBatchAction('skip')}
            disabled={actionLoading}
          >
            <SkipForward className="size-3.5 mr-1" />
            Skip
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setSelectedIds(new Set())}
          >
            Clear
          </Button>
        </div>
      )}

      {/* Empty state */}
      {!loading && filteredClips.length === 0 && (
        <div className="py-16 text-center space-y-3">
          <p className="text-muted-foreground">No clips pending.</p>
          {(target === 'shorts' ? approvedCount > 0 : selectedIds.size > 0) ? (
            target === 'compilation' ? (
              <Button onClick={handleProcessCompilation} disabled={processing || selectedIds.size < 2}>
                {processing ? <Loader2 className="size-4 animate-spin mr-1" /> : null}
                Process for Compilation
                <ChevronRight className="size-4 ml-1" />
              </Button>
            ) : (
              <Button onClick={() => navigate('/edit')}>
                Continue to Edit
                <ChevronRight className="size-4 ml-1" />
              </Button>
            )
          ) : (
            <Button variant="outline" onClick={() => navigate('/')}>
              Fetch from Home
            </Button>
          )}
        </div>
      )}

      {/* Clip grid */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {filteredClips.map((clip) => (
          <ClipCard
            key={clip.id}
            mode={target}
            clip={clip}
            selected={selectedIds.has(clip.id)}
            onToggleSelect={() => toggleSelect(clip.id)}
            onApprove={() => handleAction([clip.id], 'approve')}
            onSkip={() => handleAction([clip.id], 'skip')}
            disabled={actionLoading}
          />
        ))}
      </div>

      {/* After all reviewed */}
      {!loading && filteredClips.length === 0 && (target === 'shorts' ? approvedCount > 0 : selectedIds.size > 1) && (
        <div className="flex justify-center pt-4">
          {target === 'compilation' ? (
            <Button onClick={handleProcessCompilation} size="lg" disabled={processing || selectedIds.size < 2}>
              {processing ? <Loader2 className="size-4 animate-spin mr-1" /> : null}
              Process for Compilation
              <ChevronRight className="size-4 ml-1" />
            </Button>
          ) : (
            <Button onClick={() => navigate('/edit')} size="lg">
              Continue to Edit
              <ChevronRight className="size-4 ml-1" />
            </Button>
          )}
        </div>
      )}
    </div>
  )
}

function ClipCard({
  mode,
  clip,
  selected,
  onToggleSelect,
  onApprove,
  onSkip,
  disabled,
}: {
  mode: ReviewTarget
  clip: ClipMeta
  selected: boolean
  onToggleSelect: () => void
  onApprove: () => void
  onSkip: () => void
  disabled: boolean
}) {
  const thumbUrl = clip.thumbnail_url || thumbnailUrl(clip.id)

  return (
    <div
      className={`group rounded-lg border overflow-hidden transition-all ${
        selected ? 'border-primary ring-1 ring-primary/25' : 'hover:border-muted-foreground/25'
      }`}
    >
      {/* Thumbnail with link to clip */}
      <a
        href={clip.url}
        target="_blank"
        rel="noopener noreferrer"
        className="relative block aspect-video bg-muted"
      >
        <img
          src={thumbUrl}
          alt={clip.title}
          className="absolute inset-0 w-full h-full object-cover"
          loading="lazy"
        />
        {/* Play overlay */}
        <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity bg-black/30">
          <ExternalLink className="size-5 text-white" />
        </div>
        {/* Duration badge */}
        <div className="absolute bottom-1.5 right-1.5 rounded bg-black/70 px-1.5 py-0.5 text-[11px] font-mono text-white">
          {formatDuration(clip.duration)}
        </div>
        {/* Score badge */}
        <div className="absolute top-1.5 right-1.5">
          <Badge
            variant="outline"
            className={`text-[11px] font-mono ${scoreBadgeColor(clip._score)}`}
          >
            {clip._score?.toFixed(0) ?? '—'}
          </Badge>
        </div>
      </a>

      <div className="p-3 space-y-2">
        <div className="flex items-start gap-2">
          <Checkbox
            checked={selected}
            onCheckedChange={() => onToggleSelect()}
            className="mt-0.5"
          />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium leading-tight line-clamp-2">{clip.title}</p>
            <p className="text-xs text-muted-foreground mt-0.5">
              {clip.streamer} · {clip.game}
              {clip.view_count > 0 && ` · ${clip.view_count.toLocaleString()} views`}
            </p>
          </div>
        </div>

        {clip._analysis?.category && (
          <Badge variant="outline" className="text-[10px]">
            {clip._analysis.category}
          </Badge>
        )}

        {mode === 'compilation' ? (
          <Button
            size="sm"
            variant={selected ? 'default' : 'outline'}
            className="w-full h-8"
            onClick={onToggleSelect}
          >
            <Check className="size-3.5 mr-1" />
            {selected ? 'Checked' : 'Check'}
          </Button>
        ) : (
          <div className="flex items-center gap-2 pt-1">
            <Button
              size="sm"
              className="flex-1 h-8"
              onClick={onApprove}
              disabled={disabled}
            >
              <Check className="size-3.5 mr-1" />
              Approve
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="flex-1 h-8"
              onClick={onSkip}
              disabled={disabled}
            >
              <X className="size-3.5 mr-1" />
              Skip
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}
