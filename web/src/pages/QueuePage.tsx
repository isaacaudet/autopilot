import { useEffect, useState, useCallback, useRef } from 'react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Upload, Play, Loader2, CheckCircle2, XCircle, ExternalLink, Pencil, Globe, RefreshCw, Smartphone, Monitor } from 'lucide-react'
import { toast } from 'sonner'
import { fetchQueue, uploadClip, uploadBatch, updateClip, getTitlePreview, publishVideos, thumbnailUrl, regenerateThumbnail } from '@/lib/api'
import type { ClipMeta } from '@/lib/types'
import { VideoPreview } from '@/components/VideoPreview'
import { CompilationBuilder } from '@/components/CompilationBuilder'
import { useChannelScope } from '@/hooks/useChannelScope'

type UploadStatus = 'idle' | 'uploading' | 'done' | 'error'

export function QueuePage() {
  const [tab, setTab] = useState<'pending' | 'output' | 'compile'>('pending')
  const [clips, setClips] = useState<ClipMeta[]>([])
  const [uploadStatuses, setUploadStatuses] = useState<Record<string, UploadStatus>>({})
  const [uploadedVideoIds, setUploadedVideoIds] = useState<Record<string, string>>({})
  const [previewClip, setPreviewClip] = useState<ClipMeta | null>(null)
  const [batchUploading, setBatchUploading] = useState(false)
  const [editingClip, setEditingClip] = useState<ClipMeta | null>(null)
  const [gameFilter, setGameFilter] = useState('')
  const [streamerFilter, setStreamerFilter] = useState('')
  const [sortBy, setSortBy] = useState('score')
  const clipCache = useRef<Record<string, ClipMeta[]>>({})
  const { channel: workspaceChannel } = useChannelScope()

  const queueTab = tab === 'compile' ? 'output' : tab

  const loadClips = useCallback(() => {
    if (tab === 'compile') return
    const opts = tab === 'output'
      ? { game: gameFilter || undefined, streamer: streamerFilter || undefined, sort: sortBy, channel: workspaceChannel !== 'all' ? workspaceChannel : undefined }
      : undefined
    const cacheKey = `${queueTab}:${workspaceChannel}:${gameFilter}:${streamerFilter}:${sortBy}`
    if (clipCache.current[cacheKey]) {
      setClips(clipCache.current[cacheKey])
    }
    fetchQueue(queueTab as 'pending' | 'output', opts).then((data) => {
      clipCache.current[cacheKey] = data
      setClips(data)
    }).catch(() => {})
  }, [tab, queueTab, gameFilter, streamerFilter, sortBy, workspaceChannel])

  useEffect(() => {
    loadClips()
  }, [loadClips])

  async function handleUpload(clip: ClipMeta) {
    setUploadStatuses((s) => ({ ...s, [clip.id]: 'uploading' }))
    try {
      const targetChannel = workspaceChannel === 'all' ? null : workspaceChannel
      const result = await uploadClip(clip.id, 'unlisted', targetChannel)
      setUploadStatuses((s) => ({ ...s, [clip.id]: 'done' }))
      if (result.video_id) {
        const videoId = result.video_id
        setUploadedVideoIds((s) => ({ ...s, [clip.id]: videoId }))
      }
      toast.success(`Uploaded${result.channel ? ` to ${result.channel}` : ''}: ${result.video_id}`)
    } catch {
      setUploadStatuses((s) => ({ ...s, [clip.id]: 'error' }))
      toast.error(`Upload failed: ${clip.title}`)
    }
  }

  async function handleBatchUpload() {
    const ids = clips.filter((c) => c.processed_path && !c.video_id).map((c) => c.id)
    if (ids.length === 0) return
    setBatchUploading(true)
    try {
      const targetChannel = workspaceChannel === 'all' ? null : workspaceChannel
      await uploadBatch(ids, 'unlisted', targetChannel)
      toast.success(`Batch upload started for ${ids.length} clips`)
      loadClips()
    } catch {
      toast.error('Batch upload failed')
    } finally {
      setBatchUploading(false)
    }
  }

  async function handlePublish(videoId: string) {
    try {
      await publishVideos([videoId])
      toast.success('Published to public')
      loadClips()
    } catch {
      toast.error('Publish failed')
    }
  }

  function handleTitleSaved() {
    setEditingClip(null)
    loadClips()
  }

  const uploadableCount = clips.filter((c) => c.processed_path && !c.video_id).length

  return (
    <div className="space-y-5">
      <h1 className="text-lg font-semibold tracking-tight">Queue</h1>

      <Tabs value={tab} onValueChange={(v) => setTab(v as 'pending' | 'output' | 'compile')}>
        <div className="flex items-center justify-between">
          <TabsList>
            <TabsTrigger value="pending">Pending</TabsTrigger>
            <TabsTrigger value="output">Output</TabsTrigger>
            <TabsTrigger value="compile">Compile</TabsTrigger>
          </TabsList>
          {tab === 'output' && uploadableCount > 0 && (
            <Button
              size="sm"
              onClick={handleBatchUpload}
              disabled={batchUploading}
            >
              {batchUploading ? (
                <Loader2 className="size-4 animate-spin mr-1" />
              ) : (
                <Upload className="size-4 mr-1" />
              )}
              Upload All ({uploadableCount})
            </Button>
          )}
        </div>

        {(tab === 'pending' || tab === 'output') && (
          <p className="text-xs text-muted-foreground mt-2">
            {tab === 'pending' ? 'Clips waiting to be processed' : 'Processed clips ready for upload'}
          </p>
        )}

        {tab === 'output' && (
          <div className="flex items-center gap-3 mt-4">
            <Input
              placeholder="Filter by game..."
              aria-label="Filter by game"
              value={gameFilter}
              onChange={(e) => setGameFilter(e.target.value)}
              className="max-w-[200px]"
            />
            <Input
              placeholder="Filter by streamer..."
              aria-label="Filter by streamer"
              value={streamerFilter}
              onChange={(e) => setStreamerFilter(e.target.value)}
              className="max-w-[200px]"
            />
            <Select value={sortBy} onValueChange={setSortBy}>
              <SelectTrigger className="w-[140px]">
                <SelectValue placeholder="Sort by..." />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="score">Score</SelectItem>
                <SelectItem value="duration">Duration</SelectItem>
                <SelectItem value="views">Views</SelectItem>
                <SelectItem value="title">Title</SelectItem>
              </SelectContent>
            </Select>
          </div>
        )}

        <TabsContent value="pending" className="mt-4">
          <ClipTable
            clips={clips}
            uploadStatuses={uploadStatuses}
            uploadedVideoIds={uploadedVideoIds}
            onUpload={handleUpload}
            onPreview={setPreviewClip}
            onEditTitle={setEditingClip}
            onPublish={handlePublish}
            showUpload={false}
          />
        </TabsContent>

        <TabsContent value="output" className="mt-4">
          <ClipTable
            clips={clips}
            uploadStatuses={uploadStatuses}
            uploadedVideoIds={uploadedVideoIds}
            onUpload={handleUpload}
            onPreview={setPreviewClip}
            onEditTitle={setEditingClip}
            onPublish={handlePublish}
            showUpload
          />
        </TabsContent>

        <TabsContent value="compile" className="mt-4">
          <CompilationBuilder />
        </TabsContent>
      </Tabs>

      <VideoPreview
        clipId={previewClip?.id ?? null}
        title={previewClip?.title ?? ''}
        open={previewClip !== null}
        onOpenChange={(open) => { if (!open) setPreviewClip(null) }}
      />

      {editingClip && (
        <TitleEditDialog
          clip={editingClip}
          open={!!editingClip}
          onOpenChange={(open) => { if (!open) setEditingClip(null) }}
          onSaved={handleTitleSaved}
        />
      )}
    </div>
  )
}

function TitleEditDialog({
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
  const [generatedTitle, setGeneratedTitle] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setTitle(clip._title_override ?? clip.title)
    getTitlePreview(clip.id).then(setGeneratedTitle).catch(() => {})
  }, [clip])

  async function handleSave() {
    setSaving(true)
    try {
      await updateClip(clip.id, { title_override: title })
      toast.success('Title saved')
      onSaved()
    } catch {
      toast.error('Failed to save title')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit Upload Title</DialogTitle>
          <DialogDescription>
            {clip.streamer} — {clip.game}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={100}
              placeholder="Upload title..."
            />
            <p className="text-xs text-muted-foreground mt-1 text-right">
              {title.length}/100
            </p>
          </div>
          {generatedTitle && (
            <div className="text-xs text-muted-foreground">
              <span className="font-medium">Auto-generated:</span>{' '}
              <button
                className="underline hover:text-foreground"
                onClick={() => setTitle(generatedTitle)}
              >
                {generatedTitle}
              </button>
            </div>
          )}
          <div className="text-xs text-muted-foreground">
            <span className="font-medium">Original:</span> {clip.title}
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={saving || title.length === 0}>
            {saving && <Loader2 className="size-4 animate-spin mr-1" />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ClipTable({
  clips,
  uploadStatuses,
  uploadedVideoIds,
  onUpload,
  onPreview,
  onEditTitle,
  onPublish,
  showUpload,
}: {
  clips: ClipMeta[]
  uploadStatuses: Record<string, UploadStatus>
  uploadedVideoIds: Record<string, string>
  onUpload: (clip: ClipMeta) => void
  onPreview: (clip: ClipMeta) => void
  onEditTitle: (clip: ClipMeta) => void
  onPublish: (videoId: string) => void
  showUpload: boolean
}) {
  const [thumbBusters, setThumbBusters] = useState<Record<string, number>>({})

  async function handleRegenerate(clipId: string) {
    try {
      await regenerateThumbnail(clipId)
      setThumbBusters((prev) => ({ ...prev, [clipId]: Date.now() }))
      toast.success('Thumbnail regenerated')
    } catch {
      toast.error('Failed to regenerate thumbnail')
    }
  }

  if (clips.length === 0) {
    return <p className="text-muted-foreground text-sm">No clips.</p>
  }

  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Status</TableHead>
            {showUpload && <TableHead>Thumb</TableHead>}
            <TableHead>Streamer</TableHead>
            <TableHead>Title</TableHead>
            <TableHead className="text-right">Score</TableHead>
            <TableHead className="text-right">Duration</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {clips.map((clip) => {
            const status = uploadStatuses[clip.id] ?? 'idle'
            const videoId = clip.video_id || uploadedVideoIds[clip.id]
            const buster = thumbBusters[clip.id]
            const thumbSrc = thumbnailUrl(clip.id) + (buster ? `?t=${buster}` : '')
            return (
              <TableRow key={clip.id}>
                <TableCell>
                  <div className="flex items-center gap-1.5">
                    {clip.video_id ? (
                      <Badge variant="secondary">uploaded</Badge>
                    ) : clip.processed_path ? (
                      <Badge className="bg-primary text-primary-foreground">ready</Badge>
                    ) : (
                      <Badge variant="outline">pending</Badge>
                    )}
                    {clip.is_shorts ? (
                      <Badge variant="outline" className="text-[10px] px-1.5 gap-0.5">
                        <Smartphone className="size-2.5" /> Short
                      </Badge>
                    ) : clip.is_shorts === false ? (
                      <Badge variant="outline" className="text-[10px] px-1.5 gap-0.5">
                        <Monitor className="size-2.5" /> Landscape
                      </Badge>
                    ) : null}
                  </div>
                </TableCell>
                {showUpload && (
                  <TableCell>
                    {clip.processed_path && (
                      <button onClick={() => onPreview(clip)} className="group relative rounded overflow-hidden" aria-label={`Preview ${clip.title}`}>
                        <img
                          src={thumbSrc}
                          alt={`Thumbnail for ${clip.title}`}
                          className="w-20 h-12 object-cover transition-opacity group-hover:opacity-75"
                          onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
                        />
                        <Play className="absolute inset-0 m-auto size-5 text-white opacity-0 group-hover:opacity-100 transition-opacity" aria-hidden="true" />
                      </button>
                    )}
                  </TableCell>
                )}
                <TableCell className="font-mono text-xs">{clip.streamer}</TableCell>
                <TableCell className="max-w-xs">
                  <div className="flex items-center gap-1">
                    <span className="truncate">
                      {clip._title_override ?? clip.title}
                    </span>
                    {clip._title_override && (
                      <Badge variant="outline" className="text-[10px] shrink-0">edited</Badge>
                    )}
                  </div>
                </TableCell>
                <TableCell className="text-right font-mono">
                  {clip._score != null ? clip._score.toFixed(1) : '-'}
                </TableCell>
                <TableCell className="text-right font-mono">
                  {(clip.duration ?? 0).toFixed(1)}s
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex items-center justify-end gap-1">
                    {clip.processed_path && (
                      <Button
                        size="icon"
                        variant="ghost"
                        onClick={() => onPreview(clip)}
                        aria-label="Preview"
                      >
                        <Play className="size-4" />
                      </Button>
                    )}
                    {showUpload && clip.processed_path && (
                      <Button
                        size="icon"
                        variant="ghost"
                        onClick={() => onEditTitle(clip)}
                        aria-label="Edit title"
                      >
                        <Pencil className="size-4" />
                      </Button>
                    )}
                    {showUpload && clip.processed_path && (
                      <Button
                        size="icon"
                        variant="ghost"
                        onClick={() => handleRegenerate(clip.id)}
                        aria-label="Regenerate thumbnail"
                      >
                        <RefreshCw className="size-4" />
                      </Button>
                    )}
                    {showUpload && clip.processed_path && !clip.video_id && (
                      <>
                        {status === 'idle' && (
                          <Button
                            size="icon"
                            variant="ghost"
                            onClick={() => onUpload(clip)}
                            aria-label="Upload"
                          >
                            <Upload className="size-4" />
                          </Button>
                        )}
                        {status === 'uploading' && (
                          <Loader2 className="size-4 animate-spin text-muted-foreground" />
                        )}
                        {status === 'done' && (
                          <CheckCircle2 className="size-4 text-green-400" />
                        )}
                        {status === 'error' && (
                          <XCircle className="size-4 text-destructive" />
                        )}
                      </>
                    )}
                    {videoId && (
                      <>
                        <a
                          href={`https://youtube.com/watch?v=${videoId}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center justify-center size-8 rounded-md hover:bg-accent"
                          aria-label="View on YouTube"
                        >
                          <ExternalLink className="size-4 text-primary" />
                        </a>
                        <Button
                          size="icon"
                          variant="ghost"
                          onClick={() => onPublish(videoId)}
                          aria-label="Publish (make public)"
                        >
                          <Globe className="size-4" />
                        </Button>
                      </>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}
