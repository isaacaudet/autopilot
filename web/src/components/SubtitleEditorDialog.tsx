import { useEffect, useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { X, Loader2, RefreshCw, Plus } from 'lucide-react'
import { toast } from 'sonner'
import { fetchSubtitles, updateSubtitles, reburnSubtitles, updateClipMetadata, clipFrameUrl, type SubtitleLine } from '@/lib/api'
import type { ClipMeta } from '@/lib/types'

function addSeconds(assTime: string, seconds: number): string {
  // Parse H:MM:SS.cc
  const m = assTime.match(/^(\d+):(\d{2}):(\d{2})\.(\d{2})$/)
  if (!m) return assTime
  const total = parseInt(m[1]) * 3600 + parseInt(m[2]) * 60 + parseInt(m[3]) + parseInt(m[4]) / 100 + seconds
  const t = Math.max(0, total)
  const h = Math.floor(t / 3600)
  const min = Math.floor((t % 3600) / 60)
  const sec = Math.floor(t % 60)
  const cs = Math.round((t % 1) * 100)
  return `${h}:${String(min).padStart(2, '0')}:${String(sec).padStart(2, '0')}.${String(cs).padStart(2, '0')}`
}

interface SubtitleEditorDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  clipId: string
  clipTitle?: string
  clip?: ClipMeta
}

export function SubtitleEditorDialog({
  open,
  onOpenChange,
  clipId,
  clipTitle,
  clip,
}: SubtitleEditorDialogProps) {
  const [activeTab, setActiveTab] = useState<'subtitles' | 'position'>('subtitles')
  const [lines, setLines] = useState<SubtitleLine[]>([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [reburning, setReburning] = useState(false)
  const [hookText, setHookText] = useState('')
  const [hookDuration, setHookDuration] = useState(2.0)
  const [hookDirty, setHookDirty] = useState(false)

  // Position tab state
  const [marginV, setMarginV] = useState(450)
  const [positionDirty, setPositionDirty] = useState(false)
  const [applyingPosition, setApplyingPosition] = useState(false)

  useEffect(() => {
    if (!open || !clipId) return
    setLoading(true)
    setDirty(false)
    setActiveTab('subtitles')
    setHookText(clip?._hook_text_override ?? clip?._analysis?.hook_text as string ?? '')
    setHookDuration(clip?._hook_duration ?? 2.0)
    setHookDirty(false)
    // Initialize margin from stored value or default
    const storedMargin = (clip as Record<string, unknown> | undefined)?.subtitle_margin_v
    setMarginV(typeof storedMargin === 'number' ? storedMargin : 450)
    setPositionDirty(false)
    fetchSubtitles(clipId)
      .then(setLines)
      .catch(() => {
        toast.error('Failed to load subtitles')
        setLines([])
      })
      .finally(() => setLoading(false))
  }, [open, clipId])

  function updateLine(index: number, text: string) {
    setLines(prev =>
      prev.map((l, i) =>
        i === index ? { ...l, text, raw: '' } : l
      )
    )
    setDirty(true)
  }

  function deleteLine(index: number) {
    setLines(prev => prev.filter((_, i) => i !== index))
    setDirty(true)
  }

  function insertLine(afterIndex: number) {
    setLines(prev => {
      const newLines = [...prev]
      const prevLine = prev[afterIndex]
      const nextLine = prev[afterIndex + 1]
      const newStart = prevLine?.end ?? '0:00:00.00'
      const newEnd = nextLine?.start ?? addSeconds(newStart, 2)
      newLines.splice(afterIndex + 1, 0, {
        index: afterIndex + 1,
        start: newStart,
        end: newEnd,
        text: '',
        raw: '',
      })
      return newLines
    })
    setDirty(true)
  }

  async function saveHookSettings() {
    try {
      await updateClipMetadata(clipId, {
        hook_text_override: hookText || undefined,
        hook_duration: hookDuration,
      })
      toast.success('Hook settings saved')
      setHookDirty(false)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Save hook settings failed')
    }
  }

  async function handleSave() {
    setSaving(true)
    try {
      await updateSubtitles(clipId, lines)
      toast.success('Subtitles saved')
      setDirty(false)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  async function handleReburn() {
    setReburning(true)
    try {
      await reburnSubtitles(clipId)
      toast.success('Video re-burned with updated subtitles')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Re-burn failed')
    } finally {
      setReburning(false)
    }
  }

  async function applyPosition() {
    setApplyingPosition(true)
    try {
      await updateClipMetadata(clipId, { subtitle_margin_v: marginV })
      await reburnSubtitles(clipId)
      toast.success('Subtitle position applied and video re-burned')
      setPositionDirty(false)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Apply position failed')
    } finally {
      setApplyingPosition(false)
    }
  }

  // For the position preview, map marginV (0-800) to a CSS bottom percentage.
  // The video is 1920px tall conceptually; marginV=450 is ~23% from bottom.
  // We invert: higher marginV = closer to bottom edge = smaller bottom%.
  // preview container is 9:16 aspect. bottom% = (marginV / 800) * 60 + 5 clamped.
  const previewBottomPct = Math.max(2, Math.min(70, (marginV / 800) * 60 + 5))
  const frameUrl = clipFrameUrl(clipId)
  const previewText = lines[0]?.text || 'SUBTITLE PREVIEW TEXT'

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>Subtitle Editor</DialogTitle>
          {clipTitle && (
            <DialogDescription className="truncate">{clipTitle}</DialogDescription>
          )}
        </DialogHeader>

        {/* Tab bar */}
        <div className="flex gap-1 border-b pb-0 -mb-px">
          {(['subtitles', 'position'] as const).map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-3 py-1.5 text-xs font-medium capitalize border-b-2 transition-colors ${
                activeTab === tab
                  ? 'border-primary text-foreground'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              {tab}
            </button>
          ))}
        </div>

        {/* ── Subtitles tab ── */}
        {activeTab === 'subtitles' && (
          loading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="size-6 animate-spin text-muted-foreground" />
            </div>
          ) : lines.length === 0 ? (
            <div className="py-12 text-center text-sm text-muted-foreground">
              No subtitle lines found. Subtitles may not have been generated for this clip.
            </div>
          ) : (
            <>
              {/* Hook text + duration controls */}
              <div className="space-y-2 border-b pb-3 mb-2">
                <div className="flex items-center gap-2">
                  <label className="text-xs font-medium shrink-0 w-20">Hook text</label>
                  <Input
                    value={hookText}
                    onChange={e => { setHookText(e.target.value); setHookDirty(true) }}
                    placeholder="e.g. WATCH THIS"
                    className="text-sm h-8 flex-1"
                  />
                </div>
                <div className="flex items-center gap-2">
                  <label className="text-xs font-medium shrink-0 w-20">Duration</label>
                  <input
                    type="range"
                    min={0}
                    max={10}
                    step={0.5}
                    value={hookDuration}
                    onChange={e => { setHookDuration(parseFloat(e.target.value)); setHookDirty(true) }}
                    className="flex-1 h-2 accent-primary"
                  />
                  <span className="text-xs font-mono w-10 text-right">{hookDuration.toFixed(1)}s</span>
                </div>
                {hookDirty && (
                  <Button size="sm" variant="outline" className="h-7 text-xs" onClick={saveHookSettings}>
                    Save Hook Settings
                  </Button>
                )}
              </div>

              <div className="space-y-1.5 overflow-y-auto max-h-[50vh] pr-1">
                {lines.map((line, i) => (
                  <div key={i}>
                    <div className="flex items-center gap-2">
                      <Badge variant="outline" className="font-mono text-[10px] shrink-0 tabular-nums">
                        {line.start}
                      </Badge>
                      <Input
                        value={line.text}
                        onChange={e => updateLine(i, e.target.value)}
                        className="text-sm h-8"
                      />
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-8 shrink-0 text-muted-foreground hover:text-destructive"
                        onClick={() => deleteLine(i)}
                      >
                        <X className="size-3.5" />
                      </Button>
                    </div>
                    {i < lines.length - 1 && (
                      <div className="flex justify-center py-0.5">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-5 text-muted-foreground hover:text-foreground"
                          onClick={() => insertLine(i)}
                        >
                          <Plus className="size-3" />
                        </Button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </>
          )
        )}

        {/* ── Position tab ── */}
        {activeTab === 'position' && (
          <div className="space-y-4 overflow-y-auto">
            {/* Frame preview with subtitle overlay */}
            <div className="flex gap-4 items-start">
              {/* 9:16 preview */}
              <div
                className="relative flex-none overflow-hidden rounded-lg bg-black border"
                style={{ width: 120, height: 213 }}
              >
                <img
                  src={frameUrl}
                  alt="Clip preview"
                  className="absolute inset-0 w-full h-full object-cover"
                  onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                />
                {/* Subtitle overlay */}
                <div
                  className="absolute left-0 right-0 text-center px-1 pointer-events-none"
                  style={{
                    bottom: `${previewBottomPct}%`,
                    fontFamily: 'Impact, "Arial Narrow", sans-serif',
                    fontSize: 11,
                    color: '#ffffff',
                    textShadow: '1px 1px 2px #000, -1px -1px 2px #000, 1px -1px 2px #000, -1px 1px 2px #000',
                    textTransform: 'uppercase',
                    lineHeight: 1.2,
                    letterSpacing: '0.02em',
                  }}
                >
                  {previewText}
                </div>
              </div>

              {/* Slider */}
              <div className="flex-1 space-y-3 pt-1">
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <label className="text-xs font-medium">Subtitle position</label>
                    <span className="text-xs font-mono text-muted-foreground">{marginV}</span>
                  </div>
                  <input
                    type="range"
                    min={20}
                    max={800}
                    step={10}
                    value={marginV}
                    onChange={e => { setMarginV(parseInt(e.target.value)); setPositionDirty(true) }}
                    className="w-full h-2 accent-primary"
                  />
                  <div className="flex justify-between text-[10px] text-muted-foreground">
                    <span>Bottom</span>
                    <span>Top</span>
                  </div>
                </div>

                {positionDirty && (
                  <p className="text-[11px] text-muted-foreground">
                    Click Apply to save and re-burn the video.
                  </p>
                )}
              </div>
            </div>

            {/* Title text + position */}
            <div className="space-y-3 border-t pt-3">
              <div className="text-[11px] font-medium uppercase tracking-widest text-muted-foreground">
                Hook / Title Overlay
              </div>
              <div className="flex items-center gap-2">
                <label className="text-xs font-medium shrink-0 w-20">Title text</label>
                <Input
                  value={hookText}
                  onChange={e => { setHookText(e.target.value); setHookDirty(true) }}
                  placeholder="e.g. WATCH THIS"
                  className="text-sm h-8 flex-1"
                />
              </div>
              {hookDirty && (
                <Button size="sm" variant="outline" className="h-7 text-xs" onClick={saveHookSettings}>
                  Save Hook Settings
                </Button>
              )}
            </div>
          </div>
        )}

        <DialogFooter className="gap-2 pt-2">
          {activeTab === 'subtitles' ? (
            <>
              <span className="text-xs text-muted-foreground mr-auto">
                {lines.length} line{lines.length !== 1 ? 's' : ''}
                {dirty && ' (unsaved)'}
              </span>
              <Button
                variant="outline"
                onClick={handleReburn}
                disabled={reburning || dirty || lines.length === 0}
                className="gap-1.5"
                title={dirty ? 'Save subtitles first' : 'Re-burn video with current subtitles'}
              >
                {reburning ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
                Re-burn Video
              </Button>
              <Button
                onClick={handleSave}
                disabled={saving || !dirty || lines.length === 0}
                className="gap-1.5"
              >
                {saving && <Loader2 className="size-4 animate-spin" />}
                Save Subtitles
              </Button>
            </>
          ) : (
            <>
              <span className="text-xs text-muted-foreground mr-auto">
                {positionDirty ? 'Unsaved changes' : 'Drag slider to adjust position'}
              </span>
              <Button
                onClick={applyPosition}
                disabled={applyingPosition || !positionDirty}
                className="gap-1.5"
              >
                {applyingPosition ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
                Apply &amp; Re-burn
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
