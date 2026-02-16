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
import { X, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { fetchSubtitles, updateSubtitles, type SubtitleLine } from '@/lib/api'

interface SubtitleEditorDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  clipId: string
  clipTitle?: string
}

export function SubtitleEditorDialog({
  open,
  onOpenChange,
  clipId,
  clipTitle,
}: SubtitleEditorDialogProps) {
  const [lines, setLines] = useState<SubtitleLine[]>([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    if (!open || !clipId) return
    setLoading(true)
    setDirty(false)
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

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>Subtitle Editor</DialogTitle>
          {clipTitle && (
            <DialogDescription className="truncate">{clipTitle}</DialogDescription>
          )}
        </DialogHeader>

        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="size-6 animate-spin text-muted-foreground" />
          </div>
        ) : lines.length === 0 ? (
          <div className="py-12 text-center text-sm text-muted-foreground">
            No subtitle lines found.
          </div>
        ) : (
          <div className="space-y-1.5 overflow-y-auto max-h-[55vh] pr-1">
            {lines.map((line, i) => (
              <div key={i} className="flex items-center gap-2">
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
            ))}
          </div>
        )}

        <DialogFooter className="gap-2 pt-2">
          <span className="text-xs text-muted-foreground mr-auto">
            {lines.length} line{lines.length !== 1 ? 's' : ''}
            {dirty && ' (unsaved)'}
          </span>
          <Button
            onClick={handleSave}
            disabled={saving || !dirty || lines.length === 0}
            className="gap-1.5"
          >
            {saving && <Loader2 className="size-4 animate-spin" />}
            Save Subtitles
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
