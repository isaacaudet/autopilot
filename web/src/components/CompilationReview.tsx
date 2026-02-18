import { useEffect, useState, useMemo } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  ChevronUp, ChevronDown, X, Loader2, Upload, CheckCircle2,
} from 'lucide-react'
import { toast } from 'sonner'
import { fetchCompilationClips, fetchCompilations, fetchStudioClips, buildCompilation, uploadClip } from '@/lib/api'
import { usePipeline } from '@/hooks/usePipeline'
import type { ClipMeta } from '@/lib/types'
import { useChannelScope } from '@/hooks/useChannelScope'

const THEME_TITLES: Record<string, string> = {
  clutch: '{game} IMPOSSIBLE Clutches — Best Clips {date}',
  funny: 'Funniest {game} Moments — Try Not to Laugh {date}',
  rage: '{game} RAGE Compilation — Streamers Losing It {date}',
  skill: '{game} INSANE Plays — Top Skill Clips {date}',
  hype: '{game} HYPE Moments — Best Reactions {date}',
}

function formatTemplate(template: string, game: string): string {
  const date = new Date().toLocaleDateString('en-US', { month: '2-digit', day: '2-digit' })
  return template.replace('{game}', game.toUpperCase()).replace('{date}', date)
}

type Phase = 'review' | 'building' | 'done'

function isCompilationClip(clip: ClipMeta): boolean {
  return Boolean((clip.clip_count ?? 0) > 0) || clip.id.startsWith('compilation_')
}

export function CompilationReview() {
  const [clips, setClips] = useState<ClipMeta[]>([])
  const [ordered, setOrdered] = useState<ClipMeta[]>([])
  const [title, setTitle] = useState('')
  const [theme, setTheme] = useState('all')
  const [phase, setPhase] = useState<Phase>('review')
  const [uploading, setUploading] = useState(false)
  const { state } = usePipeline()
  const { channel: workspaceChannel } = useChannelScope()

  useEffect(() => {
    fetchCompilationClips(workspaceChannel)
      .then((clips) => {
        setClips(clips)
        // Apply countdown ordering on load
        const sorted = [...clips].sort((a, b) => (b._score ?? 0) - (a._score ?? 0))
        setOrdered(applyCountdown(sorted))
        // Set default title
        const game = clips[0]?.game ?? 'Mixed'
        const date = new Date().toLocaleDateString('en-US', { month: '2-digit', day: '2-digit' })
        setTitle(`${game.toUpperCase()} Daily Highlights — Best Clips ${date}`)
      })
      .catch(() => {})
  }, [workspaceChannel])

  // Compute available themes from clip categories
  const themes = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const c of clips) {
      const cat = c._analysis?.category
      if (cat && typeof cat === 'string') {
        counts[cat] = (counts[cat] || 0) + 1
      }
    }
    return Object.entries(counts)
      .filter(([cat, n]) => cat in THEME_TITLES && n >= 4)
      .sort((a, b) => b[1] - a[1])
  }, [clips])

  function handleThemeChange(value: string) {
    setTheme(value)
    let filtered: ClipMeta[]
    if (value === 'all') {
      filtered = [...clips].sort((a, b) => (b._score ?? 0) - (a._score ?? 0))
      const game = clips[0]?.game ?? 'Mixed'
      const date = new Date().toLocaleDateString('en-US', { month: '2-digit', day: '2-digit' })
      setTitle(`${game.toUpperCase()} Daily Highlights — Best Clips ${date}`)
    } else {
      filtered = clips
        .filter((c) => c._analysis?.category === value)
        .sort((a, b) => (b._score ?? 0) - (a._score ?? 0))
      const game = clips[0]?.game ?? 'Mixed'
      setTitle(formatTemplate(THEME_TITLES[value] ?? '{game} Highlights {date}', game))
    }
    setOrdered(applyCountdown(filtered))
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
      await buildCompilation(ordered.map((c) => c.id), title || undefined, true, targetChannel)
    } catch {
      toast.error('Failed to start compilation')
      setPhase('review')
    }
  }

  async function handleUpload() {
    // Find the most recent compilation in output
    setUploading(true)
    try {
      let comps = await fetchCompilations(workspaceChannel)

      // Back-compat fallback: include output orphans if DB index is stale.
      if (comps.length === 0) {
        const channelFilter = workspaceChannel === 'all' ? undefined : workspaceChannel
        const studio = await fetchStudioClips({ sort: 'recent', limit: 1000, channel: channelFilter })
        comps = studio.filter(isCompilationClip)
      }
      if (comps.length === 0 && workspaceChannel !== 'all') {
        const studioAll = await fetchStudioClips({ sort: 'recent', limit: 1000 })
        comps = studioAll.filter(isCompilationClip)
      }

      if (comps.length === 0) {
        toast.error('No compilation found')
        return
      }
      const latest = comps[0]
      const targetChannel = workspaceChannel === 'all' ? null : workspaceChannel
      const result = await uploadClip(latest.id, 'unlisted', targetChannel)
      toast.success(`Uploaded${result.channel ? ` to ${result.channel}` : ''}: ${result.video_id}`)
    } catch {
      toast.error('Upload failed')
    } finally {
      setUploading(false)
    }
  }

  const totalDuration = ordered.reduce((sum, c) => sum + (c.duration || 0), 0)

  // Auto-transition from building → done
  const buildProgress = state?.compile_progress ?? 0
  const buildDone = !state?.compile_step && buildProgress >= 1
  useEffect(() => {
    if (phase === 'building' && buildDone) {
      setPhase('done')
    }
  }, [phase, buildDone])

  // Building phase — show progress from SSE
  if (phase === 'building') {
    const step = state?.compile_step
    const pct = Math.round(buildProgress * 100)

    return (
      <div className="space-y-4 py-8 flex flex-col items-center">
        {buildDone ? (
          <>
            <CheckCircle2 className="size-8 text-green-400" />
            <p className="text-lg font-semibold text-green-400">Compilation complete</p>
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

  // Done phase — upload
  if (phase === 'done') {
    return (
      <div className="space-y-4 py-8 flex flex-col items-center">
        <CheckCircle2 className="size-8 text-green-400" />
        <p className="text-lg font-semibold text-green-400">Compilation complete</p>
        <p className="text-sm text-muted-foreground">{title}</p>
        <Button onClick={handleUpload} disabled={uploading}>
          {uploading ? (
            <><Loader2 className="size-4 animate-spin mr-1" />Uploading...</>
          ) : (
            <><Upload className="size-4 mr-1" />Upload to YouTube</>
          )}
        </Button>
      </div>
    )
  }

  // Review phase
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Review Compilation</h2>
          <p className="text-sm text-muted-foreground">
            {ordered.length} clips &middot; {(totalDuration / 60).toFixed(1)} min
          </p>
        </div>
        {themes.length > 0 && (
          <Select value={theme} onValueChange={handleThemeChange}>
            <SelectTrigger className="w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Mixed (all clips)</SelectItem>
              {themes.map(([cat, count]) => (
                <SelectItem key={cat} value={cat}>
                  {cat.charAt(0).toUpperCase() + cat.slice(1)} ({count} clips)
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>

      {/* Reorder table */}
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-10">#</TableHead>
              <TableHead>Title</TableHead>
              <TableHead>Streamer</TableHead>
              <TableHead className="text-right">Score</TableHead>
              <TableHead className="text-right">Duration</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {ordered.map((clip, i) => (
              <TableRow key={clip.id}>
                <TableCell className="font-mono text-muted-foreground">
                  {i + 1}
                  {i === 0 && <Badge variant="outline" className="ml-1 text-[9px]">hook</Badge>}
                  {i === ordered.length - 1 && ordered.length > 2 && (
                    <Badge variant="outline" className="ml-1 text-[9px]">finale</Badge>
                  )}
                </TableCell>
                <TableCell className="max-w-xs truncate">{clip.title}</TableCell>
                <TableCell className="font-mono text-xs">{clip.streamer}</TableCell>
                <TableCell className="text-right font-mono">
                  {clip._score != null ? clip._score.toFixed(1) : '-'}
                </TableCell>
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

      {/* Title + compile */}
      <div className="flex items-center gap-3">
        <Input
          placeholder="Compilation title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="flex-1"
        />
        <Button onClick={handleBuild} disabled={ordered.length < 2}>
          Compile ({ordered.length} clips)
        </Button>
      </div>
    </div>
  )
}

/** Apply countdown ordering: #2 as hook, worst-to-best middle, #1 as finale */
function applyCountdown(clips: ClipMeta[]): ClipMeta[] {
  if (clips.length < 3) return clips
  const best = clips[0]
  const hook = clips[1]
  const rest = clips.slice(2).reverse()
  return [hook, ...rest, best]
}
