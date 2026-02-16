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
import { batchReview, fetchQueue, thumbnailUrl } from '@/lib/api'
import type { ClipMeta } from '@/lib/types'

type SortKey = 'score' | 'views' | 'recent'

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

export function ReviewPage() {
  const navigate = useNavigate()
  const [clips, setClips] = useState<ClipMeta[]>([])
  const [loading, setLoading] = useState(true)
  const [sort, setSort] = useState<SortKey>('score')
  const [gameFilter, setGameFilter] = useState('__all__')
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [actionLoading, setActionLoading] = useState(false)
  const [approvedCount, setApprovedCount] = useState(0)
  const [skippedCount, setSkippedCount] = useState(0)

  // Pending clips don't have a channel assigned yet — don't filter by workspace channel.
  const load = useCallback(async () => {
    setLoading(true)
    setClips([])
    setSelectedIds(new Set())
    setApprovedCount(0)
    setSkippedCount(0)
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

  function toggleSelect(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleSelectAll() {
    if (selectedIds.size === filteredClips.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(filteredClips.map((c) => c.id)))
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold tracking-tight">Review</h1>
          <p className="text-sm text-muted-foreground">
            Approve or skip pending clips.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => load()} disabled={loading}>
            {loading ? <Loader2 className="size-4 animate-spin" /> : 'Refresh'}
          </Button>
        </div>
      </div>

      {/* Progress bar */}
      <div className="text-sm text-muted-foreground">
        <span className="text-green-400 font-medium">{approvedCount} approved</span>
        {' · '}
        <span>{skippedCount} skipped</span>
        {' · '}
        <span>{remaining} remaining</span>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
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

        <div className="ml-auto flex items-center gap-2">
          <Button size="sm" variant="ghost" onClick={toggleSelectAll}>
            {selectedIds.size === filteredClips.length ? 'Deselect all' : 'Select all'}
          </Button>
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
        </div>
      </div>

      {/* Selection bar */}
      {selectedIds.size > 0 && (
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
          {approvedCount > 0 ? (
            <Button onClick={() => navigate('/edit')}>
              Continue to Edit
              <ChevronRight className="size-4 ml-1" />
            </Button>
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
      {!loading && filteredClips.length === 0 && approvedCount > 0 && (
        <div className="flex justify-center pt-4">
          <Button onClick={() => navigate('/edit')} size="lg">
            Continue to Edit
            <ChevronRight className="size-4 ml-1" />
          </Button>
        </div>
      )}
    </div>
  )
}

function ClipCard({
  clip,
  selected,
  onToggleSelect,
  onApprove,
  onSkip,
  disabled,
}: {
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
      </div>
    </div>
  )
}
