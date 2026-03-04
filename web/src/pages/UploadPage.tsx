import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Loader2, Upload, Video } from 'lucide-react'
import { toast } from 'sonner'
import { ingestVideo, pollIngestStatus } from '@/lib/api'

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function UploadPage() {
  const navigate = useNavigate()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const [pollingClipId, setPollingClipId] = useState<string | null>(null)

  // Poll for transcription completion
  useEffect(() => {
    if (!pollingClipId) return
    let cancelled = false

    const poll = async () => {
      while (!cancelled) {
        await new Promise(r => setTimeout(r, 1500))
        if (cancelled) break
        try {
          const status = await pollIngestStatus(pollingClipId)
          if (status.status === 'done') {
            setTranscribing(false)
            toast.success('Transcription complete')
            navigate(`/edit?clip=${pollingClipId}`)
            break
          } else if (status.status === 'error') {
            setTranscribing(false)
            toast.error(`Transcription failed: ${status.error ?? 'unknown error'}`)
            // Still navigate so the clip can be edited
            navigate(`/edit?clip=${pollingClipId}`)
            break
          }
          // Still transcribing — keep polling
        } catch (err) {
          setTranscribing(false)
          toast.error(err instanceof Error ? err.message : 'Status check failed')
          break
        }
      }
    }

    poll()
    return () => { cancelled = true }
  }, [pollingClipId, navigate])

  function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return
    const f = files[0]
    if (!f.type.startsWith('video/')) {
      toast.error('Please select a video file')
      return
    }
    setFile(f)
    if (!title) setTitle(f.name.replace(/\.[^/.]+$/, ''))
  }

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(true)
  }, [])

  const onDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
  }, [])

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    handleFiles(e.dataTransfer.files)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  async function handleUpload() {
    if (!file) return
    setUploading(true)
    try {
      const result = await ingestVideo(file, title || undefined)
      setUploading(false)
      setTranscribing(true)
      setPollingClipId(result.clip_id)
      toast.info('Upload complete — transcribing…')
    } catch (err) {
      setUploading(false)
      toast.error(err instanceof Error ? err.message : 'Upload failed')
    }
  }

  const busy = uploading || transcribing

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Upload</h1>
        <p className="text-xs text-muted-foreground mt-0.5">
          Ingest a local video and auto-transcribe subtitles.
        </p>
      </div>

      <div className="max-w-lg space-y-4">
        {/* Drop zone */}
        <div
          onClick={() => !busy && fileInputRef.current?.click()}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          className={`
            relative flex flex-col items-center justify-center gap-3
            rounded-xl border-2 border-dashed p-10 transition-colors cursor-pointer
            ${dragging ? 'border-primary bg-primary/5' : 'border-border hover:border-primary/50 hover:bg-muted/30'}
            ${busy ? 'pointer-events-none opacity-60' : ''}
          `}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept="video/*"
            className="sr-only"
            onChange={e => handleFiles(e.target.files)}
          />

          {file ? (
            <>
              <Video className="size-10 text-primary" />
              <div className="text-center space-y-0.5">
                <p className="text-sm font-medium truncate max-w-[280px]">{file.name}</p>
                <p className="text-xs text-muted-foreground">{formatBytes(file.size)}</p>
              </div>
              {!busy && (
                <p className="text-xs text-muted-foreground">Click to change file</p>
              )}
            </>
          ) : (
            <>
              <Upload className="size-10 text-muted-foreground" />
              <div className="text-center space-y-0.5">
                <p className="text-sm font-medium">Drop a video here</p>
                <p className="text-xs text-muted-foreground">or click to browse</p>
              </div>
            </>
          )}
        </div>

        {/* Title input */}
        {file && (
          <Card>
            <CardHeader className="pb-2 pt-4 px-4">
              <CardTitle className="text-sm">Title</CardTitle>
              <CardDescription className="text-xs">
                Optional — used as the clip title in the editor
              </CardDescription>
            </CardHeader>
            <CardContent className="pb-4 px-4">
              <Input
                value={title}
                onChange={e => setTitle(e.target.value)}
                placeholder="e.g. Clutch play vs top team"
                className="text-sm"
                disabled={busy}
              />
            </CardContent>
          </Card>
        )}

        {/* Upload button */}
        {file && (
          <Button
            onClick={handleUpload}
            disabled={busy}
            className="w-full gap-2"
            size="lg"
          >
            {uploading ? (
              <>
                <Loader2 className="size-4 animate-spin" />
                Uploading…
              </>
            ) : transcribing ? (
              <>
                <Loader2 className="size-4 animate-spin" />
                Transcribing… (this may take a minute)
              </>
            ) : (
              <>
                <Upload className="size-4" />
                Upload &amp; Transcribe
              </>
            )}
          </Button>
        )}
      </div>
    </div>
  )
}
