import { useEffect, useState, useCallback, useRef } from 'react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  SheetFooter,
} from '@/components/ui/sheet'
import {
  Upload,
  Play,
  Loader2,
  ExternalLink,
  Pencil,
  Globe,
  Smartphone,
  Monitor,
  Film,
  X,
  Save,
  RefreshCw,
  Brain,
  FolderOpen,
  Wrench,
  Crop,
  ChevronDown,
  ChevronUp,
  Captions,
} from 'lucide-react'
import { toast } from 'sonner'
import {
  fetchStudioClips,
  uploadClip,
  publishClip,
  thumbnailUrl,
  videoUrl,
  updateClipMetadata,
  getTitlePreview,
  getDescriptionPreview,
  getTagsPreview,
  collectPerformance,
  trainWeights,
  fetchLearnStatus,
  openOutputClip,
  openOutputFolder,
  resyncOutput,
} from '@/lib/api'
import type { ClipMeta } from '@/lib/types'
import { usePipeline } from '@/hooks/usePipeline'
import { useChannelScope } from '@/hooks/useChannelScope'
import { CropProfilesDialog } from '@/components/CropProfilesDialog'
import { SubtitleEditorDialog } from '@/components/SubtitleEditorDialog'

type UploadStatus = 'idle' | 'uploading' | 'done' | 'error'
type TypeFilter = 'all' | 'shorts' | 'landscape' | 'compilation'
type StatusFilter = 'all' | 'ready' | 'uploaded'

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return m > 0 ? `${m}:${s.toString().padStart(2, '0')}` : `${s}s`
}

export function StudioPage() {
  const [clips, setClips] = useState<ClipMeta[]>([])
  const [loading, setLoading] = useState(true)
  const [uploadStatuses, setUploadStatuses] = useState<Record<string, UploadStatus>>({})
  const [playingClipId, setPlayingClipId] = useState<string | null>(null)
  const [editingClip, setEditingClip] = useState<ClipMeta | null>(null)
  const [gameFilter, setGameFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [sortBy, setSortBy] = useState('recent')
  const [uploadChannel, setUploadChannel] = useState<string>(() => {
    try {
      return localStorage.getItem('clipper.uploadChannel') || '__auto__'
    } catch {
      return '__auto__'
    }
  })
  const [learnStatus, setLearnStatus] = useState<{ learned: boolean; sample_count: number } | null>(null)
  const [retraining, setRetraining] = useState(false)
  const [resyncing, setResyncing] = useState(false)
  const [latestOnly, setLatestOnly] = useState(false)
  const [cropsOpen, setCropsOpen] = useState(false)
  const [subtitleClip, setSubtitleClip] = useState<ClipMeta | null>(null)
  const { state: pipeline } = usePipeline()
  const { channel: workspaceChannel, channels: channelConfig } = useChannelScope()

  // Load learning status on mount
  useEffect(() => {
    fetchLearnStatus().then(setLearnStatus).catch(() => {})
  }, [])

  useEffect(() => {
    try {
      localStorage.setItem('clipper.uploadChannel', uploadChannel)
    } catch {
      // ignore storage failures (private mode, etc.)
    }
  }, [uploadChannel])

  async function handleRetrain() {
    setRetraining(true)
    try {
      const { collected } = await collectPerformance()
      toast.info(`Collected ${collected} new sample${collected !== 1 ? 's' : ''}`)
      const result = await trainWeights()
      toast.success(`Weights trained on ${(result as { sample_size?: number }).sample_size ?? '?'} samples`)
      fetchLearnStatus().then(setLearnStatus).catch(() => {})
      loadClips()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Retrain failed')
    } finally {
      setRetraining(false)
    }
  }

  async function handleResync() {
    setResyncing(true)
    try {
      const result = await resyncOutput(300)
      toast.success(`Reindexed ${result.created} output clips`)
      await loadClips()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Output reindex failed')
    } finally {
      setResyncing(false)
    }
  }

  async function handleOpenOutputFolder() {
    try {
      await openOutputFolder()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to open output folder')
    }
  }

  const loadClips = useCallback(async () => {
    try {
      const data = await fetchStudioClips({
        game: gameFilter || undefined,
        sort: sortBy,
        channel: workspaceChannel,
      })
      setClips(data)
    } catch {
      toast.error('Failed to load clips')
    } finally {
      setLoading(false)
    }
  }, [gameFilter, sortBy, workspaceChannel])

  useEffect(() => {
    loadClips()
  }, [loadClips])

  // Auto-reload when pipeline finishes processing
  const prevRunningRef = useRef(pipeline?.running ?? false)
  useEffect(() => {
    const wasRunning = prevRunningRef.current
    const isRunning = pipeline?.running ?? false
    prevRunningRef.current = isRunning
    if (wasRunning && !isRunning) {
      loadClips()
    }
  }, [pipeline?.running, loadClips])

  // Client-side filters for type and status
  const latestIds = new Set((pipeline?.completed_clip_ids ?? []).filter(Boolean))
  const filteredClips = clips.filter((clip) => {
    if (latestOnly && latestIds.size > 0 && !latestIds.has(clip.id)) return false
    if (typeFilter === 'shorts' && !clip.is_shorts) return false
    if (typeFilter === 'landscape' && (clip.is_shorts || clip.clip_count)) return false
    if (typeFilter === 'compilation' && !clip.clip_count) return false
    const uploaded = !!clip.video_id
    if (statusFilter === 'ready' && uploaded) return false
    if (statusFilter === 'uploaded' && !uploaded) return false
    return true
  })

  const channels = Object.entries(channelConfig ?? {})
  const workspaceLabel =
    workspaceChannel === 'all'
      ? 'All channels'
      : channelConfig?.[workspaceChannel]?.name ?? workspaceChannel

  async function handleUpload(clip: ClipMeta) {
    setUploadStatuses((s) => ({ ...s, [clip.id]: 'uploading' }))
    try {
      const workspaceKey = workspaceChannel === 'all' ? null : workspaceChannel
      const selectedChannel =
        uploadChannel === '__autoroute__'
          ? null
          : uploadChannel === '__auto__'
            ? workspaceKey
            : uploadChannel
      const result = await uploadClip(clip.id, 'unlisted', selectedChannel)
      setUploadStatuses((s) => ({ ...s, [clip.id]: 'done' }))
      toast.success(`Uploaded${result.channel ? ` to ${result.channel}` : ''}: ${result.video_id}`)
      loadClips()
    } catch (err) {
      setUploadStatuses((s) => ({ ...s, [clip.id]: 'error' }))
      toast.error(err instanceof Error ? err.message : 'Upload failed')
    }
  }

  async function handlePublish(clipId: string) {
    try {
      const result = await publishClip(clipId)
      toast.success(`Published${result.channel ? ` (${result.channel})` : ''}`)
      loadClips()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Publish failed')
    }
  }

  function handleMetadataSaved() {
    setEditingClip(null)
    loadClips()
  }

  return (
    <div className="space-y-5">
      <h1 className="text-lg font-semibold tracking-tight">Studio</h1>

      {workspaceChannel !== 'all' && (
        <div className="rounded-lg border bg-muted/10 px-3 py-2 text-xs text-muted-foreground">
          Showing output for <span className="text-foreground">{workspaceLabel}</span>. Switch workspace to All channels to see unassigned clips.
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <Input
          placeholder="Filter by game..."
          value={gameFilter}
          onChange={(e) => setGameFilter(e.target.value)}
          className="max-w-[180px]"
        />
        <Select value={typeFilter} onValueChange={(v) => setTypeFilter(v as TypeFilter)}>
          <SelectTrigger className="w-[140px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All types</SelectItem>
            <SelectItem value="shorts">Shorts</SelectItem>
            <SelectItem value="landscape">Landscape</SelectItem>
            <SelectItem value="compilation">Compilation</SelectItem>
          </SelectContent>
        </Select>
        <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v as StatusFilter)}>
          <SelectTrigger className="w-[140px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All status</SelectItem>
            <SelectItem value="ready">Ready</SelectItem>
            <SelectItem value="uploaded">Uploaded</SelectItem>
          </SelectContent>
        </Select>
        <Select value={sortBy} onValueChange={setSortBy}>
          <SelectTrigger className="w-[130px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="recent">Recent</SelectItem>
            <SelectItem value="score">Score</SelectItem>
            <SelectItem value="duration">Duration</SelectItem>
            <SelectItem value="views">Views</SelectItem>
            <SelectItem value="title">Title</SelectItem>
          </SelectContent>
        </Select>
        <Select value={uploadChannel} onValueChange={setUploadChannel}>
          <SelectTrigger className="w-[170px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__auto__">Upload: Workspace channel</SelectItem>
            <SelectItem value="__autoroute__">Upload: Auto route</SelectItem>
            {channels.map(([key, value]) => (
              <SelectItem key={key} value={key}>
                Upload: {value.name ?? key}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          size="sm"
          variant={latestOnly ? 'default' : 'outline'}
          className="h-9 text-xs"
          onClick={() => setLatestOnly((v) => !v)}
          disabled={(pipeline?.completed_clip_ids?.length ?? 0) === 0}
          title={latestOnly ? 'Showing most recent pipeline outputs' : 'Filter to most recent pipeline outputs'}
        >
          Latest run
        </Button>
        <div className="ml-auto flex items-center gap-3">
          {learnStatus && (
            <span className="text-[11px] text-muted-foreground">
              Scoring: {learnStatus.learned ? `learned (${learnStatus.sample_count} samples)` : 'defaults'}
            </span>
          )}
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs gap-1"
            onClick={handleRetrain}
            disabled={retraining}
          >
            {retraining ? <Loader2 className="size-3 animate-spin" /> : <Brain className="size-3" />}
            Retrain
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs gap-1"
            onClick={handleOpenOutputFolder}
          >
            <FolderOpen className="size-3" />
            Output
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs gap-1"
            onClick={() => setCropsOpen(true)}
            disabled={clips.length === 0}
            title="Calibrate facecam + HUD crops per streamer (used by Fill portrait layout)"
          >
            <Crop className="size-3" />
            Crops
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs gap-1"
            onClick={handleResync}
            disabled={resyncing}
          >
            {resyncing ? <Loader2 className="size-3 animate-spin" /> : <Wrench className="size-3" />}
            Reindex
          </Button>
          <span className="text-xs text-muted-foreground">
            {filteredClips.length} clip{filteredClips.length !== 1 ? 's' : ''}
          </span>
        </div>
      </div>

      {/* Card grid */}
      {loading ? (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="size-6 animate-spin text-muted-foreground" />
        </div>
      ) : filteredClips.length === 0 ? (
        <div className="py-10 text-center space-y-3">
          <p className="text-sm text-muted-foreground">No clips match your filters.</p>
          <Button variant="outline" size="sm" onClick={handleResync} disabled={resyncing}>
            {resyncing ? <Loader2 className="size-4 animate-spin mr-1" /> : <Wrench className="size-4 mr-1" />}
            Reindex Output Folder
          </Button>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredClips.map((clip) => (
            <StudioCard
              key={clip.id}
              clip={clip}
              uploadStatus={uploadStatuses[clip.id] ?? 'idle'}
              isPlaying={playingClipId === clip.id}
              onPlay={() => setPlayingClipId(playingClipId === clip.id ? null : clip.id)}
              onStopPlay={() => setPlayingClipId(null)}
              onUpload={() => handleUpload(clip)}
              onPublish={() => handlePublish(clip.id)}
              onEdit={() => setEditingClip(clip)}
              onReveal={() => openOutputClip(clip.id).catch((err) => toast.error(err instanceof Error ? err.message : 'Reveal failed'))}
              onSubtitles={() => setSubtitleClip(clip)}
            />
          ))}
        </div>
      )}

      {/* Metadata sheet */}
      {editingClip && (
        <MetadataSheet
          clip={editingClip}
          open={!!editingClip}
          onOpenChange={(open) => { if (!open) setEditingClip(null) }}
          onSaved={handleMetadataSaved}
        />
      )}

      <CropProfilesDialog open={cropsOpen} onOpenChange={setCropsOpen} clips={clips} />

      {subtitleClip && (
        <SubtitleEditorDialog
          open={!!subtitleClip}
          onOpenChange={(open) => { if (!open) setSubtitleClip(null) }}
          clipId={subtitleClip.id}
          clipTitle={subtitleClip._title_override ?? subtitleClip._generated_title ?? subtitleClip.title}
        />
      )}
    </div>
  )
}

function StudioCard({
  clip,
  uploadStatus,
  isPlaying,
  onPlay,
  onStopPlay,
  onUpload,
  onPublish,
  onEdit,
  onReveal,
  onSubtitles,
}: {
  clip: ClipMeta
  uploadStatus: UploadStatus
  isPlaying: boolean
  onPlay: () => void
  onStopPlay: () => void
  onUpload: () => void
  onPublish: () => void
  onEdit: () => void
  onReveal: () => void
  onSubtitles: () => void
}) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [videoLoading, setVideoLoading] = useState(false)
  const [videoError, setVideoError] = useState<string | null>(null)

  useEffect(() => {
    if (isPlaying) {
      setVideoLoading(true)
      setVideoError(null)
    } else {
      setVideoLoading(false)
      setVideoError(null)
    }
  }, [isPlaying])

  const displayTitle = clip._title_override ?? clip._generated_title ?? clip.title
  const showOriginalTitle = clip.title && displayTitle && clip.title !== displayTitle
  const targetChannel = clip._target_channel
  const uploadedChannel = clip.channel
  const showTargetChannel = Boolean(targetChannel)
  const showUploadedChannel = Boolean(uploadedChannel) && uploadedChannel !== targetChannel

  return (
    <div className="group rounded-lg border bg-card overflow-hidden">
      {/* Thumbnail / inline video */}
      <div className="relative aspect-video bg-muted">
        {isPlaying ? (
          <>
            <video
              ref={videoRef}
              src={videoUrl(clip.id)}
              controls
              autoPlay
              className="w-full h-full object-contain bg-black"
              onLoadedData={() => setVideoLoading(false)}
              onCanPlay={() => setVideoLoading(false)}
              onError={() => {
                setVideoLoading(false)
                setVideoError('Failed to load video preview')
              }}
            />
            {videoLoading && (
              <div className="absolute inset-0 flex items-center justify-center bg-black/50">
                <Loader2 className="size-6 animate-spin text-white" />
              </div>
            )}
            {videoError && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/60 p-4 text-center">
                <p className="text-xs text-white/90">{videoError}</p>
                <Button size="sm" variant="secondary" onClick={onReveal} className="h-7 text-xs">
                  <FolderOpen className="size-3 mr-1" />
                  Reveal file
                </Button>
              </div>
            )}
            <button
              onClick={onStopPlay}
              className="absolute top-2 right-2 rounded-full bg-black/60 p-1 text-white hover:bg-black/80"
              aria-label="Close video"
            >
              <X className="size-3.5" />
            </button>
          </>
        ) : (
          <button onClick={onPlay} className="relative w-full h-full" aria-label={`Play ${clip.title}`}>
            <img
              src={thumbnailUrl(clip.id)}
              alt=""
              className="w-full h-full object-cover"
              onError={(e) => {
                ;(e.target as HTMLImageElement).style.display = 'none'
              }}
            />
            <div className="absolute inset-0 flex items-center justify-center bg-black/0 group-hover:bg-black/20 transition-colors">
              <Play className="size-10 text-white opacity-0 group-hover:opacity-90 transition-opacity drop-shadow-lg" />
            </div>
            {/* Duration badge */}
            <span className="absolute bottom-2 right-2 rounded bg-black/70 px-1.5 py-0.5 text-[10px] font-mono text-white">
              {formatDuration(clip.duration ?? 0)}
            </span>
          </button>
        )}
      </div>

      {/* Content */}
      <div className="p-3 space-y-2">
        {/* Status badges */}
        <div className="flex items-center gap-1.5 flex-wrap">
          {clip.video_id ? (
            <Badge variant="secondary" className="text-[10px]">uploaded</Badge>
          ) : (
            <Badge className="bg-primary text-primary-foreground text-[10px]">ready</Badge>
          )}
          {showTargetChannel && (
            <Badge variant="outline" className="text-[10px] px-1.5 font-mono">
              target:{targetChannel}
            </Badge>
          )}
          {showUploadedChannel && (
            <Badge variant="outline" className="text-[10px] px-1.5 font-mono">
              uploaded:{uploadedChannel}
            </Badge>
          )}
          {clip.is_shorts ? (
            <Badge variant="outline" className="text-[10px] px-1.5 gap-0.5">
              <Smartphone className="size-2.5" /> Short
            </Badge>
          ) : clip.clip_count ? (
            <Badge variant="outline" className="text-[10px] px-1.5 gap-0.5">
              <Film className="size-2.5" /> {clip.clip_count} clips
            </Badge>
          ) : (
            <Badge variant="outline" className="text-[10px] px-1.5 gap-0.5">
              <Monitor className="size-2.5" /> Landscape
            </Badge>
          )}
          {clip._title_override && (
            <Badge variant="outline" className="text-[10px]">edited</Badge>
          )}
        </div>

        {/* Title */}
        <div className="space-y-1">
          <p className="text-sm font-medium leading-snug line-clamp-2">
            {displayTitle}
          </p>
          {showOriginalTitle && (
            <p className="text-[11px] text-muted-foreground line-clamp-1">
              Source: {clip.title}
            </p>
          )}
        </div>

        {/* Metadata line */}
        <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
          {clip.streamer && <span className="font-mono">{clip.streamer}</span>}
          {clip.game && (
            <>
              <span className="text-border">|</span>
              <span>{clip.game}</span>
            </>
          )}
          {clip._score != null && (
            <>
              <span className="text-border">|</span>
              <span>{clip._score.toFixed(1)} pts</span>
            </>
          )}
          {clip.view_count > 0 && (
            <>
              <span className="text-border">|</span>
              <span>{clip.view_count.toLocaleString()} twitch views</span>
            </>
          )}
          {Array.isArray(clip._generated_tags) && clip._generated_tags.length > 0 && (
            <>
              <span className="text-border">|</span>
              <span>{clip._generated_tags.length} tags</span>
            </>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1 pt-1">
          {!clip.video_id && (
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs gap-1"
              onClick={onUpload}
              disabled={uploadStatus === 'uploading'}
            >
              {uploadStatus === 'uploading' ? (
                <Loader2 className="size-3 animate-spin" />
              ) : (
                <Upload className="size-3" />
              )}
              Upload
            </Button>
          )}
          {clip.video_id && clip.video_id !== 'previously_uploaded' && (
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs gap-1"
              onClick={onPublish}
            >
              <Globe className="size-3" />
              Publish
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            className="h-7 text-xs gap-1"
            onClick={onEdit}
          >
            <Pencil className="size-3" />
            Edit
          </Button>
          {(clip.is_shorts || clip._subtitle_path) && (
            <Button
              size="sm"
              variant="ghost"
              className="h-7 text-xs gap-1"
              onClick={onSubtitles}
            >
              <Captions className="size-3" />
              Subs
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            className="h-7 text-xs gap-1"
            onClick={onReveal}
          >
            <FolderOpen className="size-3" />
            File
          </Button>
          {clip.video_id && clip.video_id !== 'previously_uploaded' && (
            <a
              href={`https://youtube.com/watch?v=${clip.video_id}`}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-auto inline-flex items-center justify-center size-7 rounded-md hover:bg-accent"
              aria-label="View on YouTube"
            >
              <ExternalLink className="size-3.5 text-primary" />
            </a>
          )}
        </div>
      </div>
    </div>
  )
}

function MetadataSheet({
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
  const [title, setTitle] = useState(clip._title_override ?? clip._generated_title ?? clip.title)
  const [description, setDescription] = useState(clip._description_override ?? clip._generated_description ?? '')
  const [tags, setTags] = useState<string[]>(clip._tags_override ?? clip._generated_tags ?? [])
  const [tagInput, setTagInput] = useState('')
  const [saving, setSaving] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [descExpanded, setDescExpanded] = useState(!clip.clip_count)

  // Auto-generated suggestions
  const [generatedTitle, setGeneratedTitle] = useState<string | null>(clip._generated_title ?? null)
  const [generatedDescription, setGeneratedDescription] = useState<string | null>(clip._generated_description ?? null)
  const [generatedTags, setGeneratedTags] = useState<string[] | null>(clip._generated_tags ?? null)

  useEffect(() => {
    setTitle(clip._title_override ?? clip._generated_title ?? clip.title)
    setDescription(clip._description_override ?? clip._generated_description ?? '')
    setTags(clip._tags_override ?? clip._generated_tags ?? [])
    setTagInput('')
    setDescExpanded(!clip.clip_count)

    // Fetch auto-generated suggestions
    if (clip._generated_title) setGeneratedTitle(clip._generated_title)
    else getTitlePreview(clip.id).then(setGeneratedTitle).catch(() => {})

    if (clip._generated_description) setGeneratedDescription(clip._generated_description)
    else getDescriptionPreview(clip.id).then(setGeneratedDescription).catch(() => {})

    if (clip._generated_tags) setGeneratedTags(clip._generated_tags)
    else getTagsPreview(clip.id).then(setGeneratedTags).catch(() => {})
  }, [clip])

  async function handleSave(syncToYoutube: boolean) {
    const setter = syncToYoutube ? setSyncing : setSaving
    setter(true)
    try {
      await updateClipMetadata(clip.id, {
        title_override: title || undefined,
        description_override: description || undefined,
        tags_override: tags.length > 0 ? tags : undefined,
        sync_youtube: syncToYoutube,
      })
      toast.success(syncToYoutube ? 'Saved & synced to YouTube' : 'Saved locally')
      onSaved()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Save failed')
    } finally {
      setter(false)
    }
  }

  function addTag() {
    const t = tagInput.trim()
    if (t && !tags.includes(t)) {
      setTags([...tags, t])
    }
    setTagInput('')
  }

  function removeTag(tag: string) {
    setTags(tags.filter((t) => t !== tag))
  }

  function useGeneratedTags() {
    if (generatedTags) setTags(generatedTags)
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="sm:max-w-lg overflow-y-auto">
        <SheetHeader>
          <SheetTitle>Edit Metadata</SheetTitle>
          <SheetDescription>
            {clip.streamer} — {clip.game}
            {clip.video_id && (
              <span className="ml-2 text-xs text-primary">YouTube: {clip.video_id}</span>
            )}
          </SheetDescription>
        </SheetHeader>

        <div className="space-y-5 px-4">
          {/* Title */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Title</label>
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={100}
              placeholder="Video title..."
            />
            <div className="flex items-center justify-between">
              <span className="text-[11px] text-muted-foreground">{title.length}/100</span>
              {generatedTitle && title !== generatedTitle && (
                <button
                  className="text-[11px] text-muted-foreground underline hover:text-foreground"
                  onClick={() => setTitle(generatedTitle)}
                >
                  Use auto-generated
                </button>
              )}
            </div>
          </div>

          {/* Description */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <label className="text-sm font-medium">Description</label>
              {description.length > 500 && (
                <button
                  className="text-[11px] text-muted-foreground underline hover:text-foreground flex items-center gap-0.5"
                  onClick={() => setDescExpanded(!descExpanded)}
                >
                  {descExpanded ? (
                    <><ChevronUp className="size-3" /> Collapse</>
                  ) : (
                    <><ChevronDown className="size-3" /> Show full ({description.length} chars)</>
                  )}
                </button>
              )}
            </div>
            {descExpanded ? (
              <Textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                maxLength={5000}
                rows={6}
                placeholder="Video description..."
                className="resize-y"
              />
            ) : (
              <div
                className="rounded-md border bg-muted/50 p-3 text-sm text-muted-foreground cursor-pointer whitespace-pre-wrap"
                onClick={() => setDescExpanded(true)}
              >
                {description.length > 500 ? description.slice(0, 500) + '...' : description || 'No description'}
              </div>
            )}
            <div className="flex items-center justify-between">
              <span className="text-[11px] text-muted-foreground">{description.length}/5000</span>
              {generatedDescription && description !== generatedDescription && (
                <button
                  className="text-[11px] text-muted-foreground underline hover:text-foreground"
                  onClick={() => setDescription(generatedDescription)}
                >
                  Use auto-generated
                </button>
              )}
            </div>
          </div>

          {/* Tags */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <label className="text-sm font-medium">Tags</label>
              {generatedTags && tags.length === 0 && (
                <button
                  className="text-[11px] text-muted-foreground underline hover:text-foreground"
                  onClick={useGeneratedTags}
                >
                  Load auto-generated ({generatedTags.length})
                </button>
              )}
            </div>
            <div className="flex flex-wrap gap-1.5 min-h-[32px]">
              {tags.map((tag) => (
                <Badge
                  key={tag}
                  variant="secondary"
                  className="gap-1 pr-1 cursor-pointer hover:bg-destructive/20"
                  onClick={() => removeTag(tag)}
                >
                  {tag}
                  <X className="size-3" />
                </Badge>
              ))}
            </div>
            <div className="flex gap-2">
              <Input
                value={tagInput}
                onChange={(e) => setTagInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    addTag()
                  }
                }}
                placeholder="Add tag..."
                className="flex-1"
              />
              <Button size="sm" variant="outline" onClick={addTag} disabled={!tagInput.trim()}>
                Add
              </Button>
            </div>
            <p className="text-[11px] text-muted-foreground">
              {tags.length} tag{tags.length !== 1 ? 's' : ''} &middot; Click a tag to remove
            </p>
          </div>
        </div>

        <SheetFooter className="gap-2">
          <Button
            variant="outline"
            onClick={() => handleSave(false)}
            disabled={saving || syncing}
            className="gap-1.5"
          >
            {saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
            Save locally
          </Button>
          {clip.video_id && clip.video_id !== 'previously_uploaded' && (
            <Button
              onClick={() => handleSave(true)}
              disabled={saving || syncing}
              className="gap-1.5"
            >
              {syncing ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
              Save & sync to YouTube
            </Button>
          )}
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
