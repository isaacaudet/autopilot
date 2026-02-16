import { useEffect, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { CalendarPlus, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { fetchReleases, fetchStudioClips, scheduleRelease } from '@/lib/api'
import type { ClipMeta, Release } from '@/lib/types'
import { useChannelScope } from '@/hooks/useChannelScope'

const statusVariant: Record<Release['status'], 'outline' | 'secondary' | 'default' | 'destructive'> = {
  pending: 'outline',
  uploaded: 'secondary',
  published: 'default',
  failed: 'destructive',
}

export function CalendarPage() {
  const [releases, setReleases] = useState<Release[]>([])
  const [loading, setLoading] = useState(true)
  const [scheduleOpen, setScheduleOpen] = useState(false)
  const { channel, channels } = useChannelScope()

  async function load() {
    setLoading(true)
    try {
      const data = await fetchReleases(channel)
      setReleases(data)
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [channel])

  const counts = {
    pending: releases.filter((r) => r.status === 'pending').length,
    uploaded: releases.filter((r) => r.status === 'uploaded').length,
    published: releases.filter((r) => r.status === 'published').length,
    failed: releases.filter((r) => r.status === 'failed').length,
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold tracking-tight">Calendar</h1>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={load} disabled={loading}>
            {loading ? <Loader2 className="size-4 animate-spin" /> : 'Refresh'}
          </Button>
          <Button onClick={() => setScheduleOpen(true)}>
            <CalendarPlus className="size-4 mr-1" />
            Schedule Release
          </Button>
        </div>
      </div>

      <p className="text-sm text-muted-foreground">
        {counts.pending} pending | {counts.uploaded} uploaded | {counts.published} published
        {counts.failed > 0 && ` | ${counts.failed} failed`}
      </p>

      {releases.length === 0 ? (
        <div className="py-10 text-center space-y-3">
          <p className="text-muted-foreground text-sm">No scheduled releases.</p>
          <Button variant="outline" onClick={() => setScheduleOpen(true)}>
            <CalendarPlus className="size-4 mr-1" />
            Schedule your first release
          </Button>
        </div>
      ) : (
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead>Time</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Channel</TableHead>
                <TableHead>Clip</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {releases.map((r, i) => {
                const dt = new Date(r.scheduled_at)
                return (
                  <TableRow key={`${r.clip_id}-${i}`}>
                    <TableCell className="font-mono text-sm">
                      {dt.toLocaleDateString()}
                    </TableCell>
                    <TableCell className="font-mono text-sm">
                      {dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </TableCell>
                    <TableCell>
                      <Badge variant={statusVariant[r.status]}>
                        {r.status}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      {channels?.[r.channel]?.name ?? r.channel}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{r.clip_id}</TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      )}

      <ScheduleDialog
        open={scheduleOpen}
        onOpenChange={setScheduleOpen}
        channels={channels}
        defaultChannel={channel !== 'all' ? channel : undefined}
        onScheduled={() => {
          setScheduleOpen(false)
          load()
        }}
      />
    </div>
  )
}

function ScheduleDialog({
  open,
  onOpenChange,
  channels,
  defaultChannel,
  onScheduled,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  channels: Record<string, { name?: string }> | null
  defaultChannel?: string
  onScheduled: () => void
}) {
  const [clips, setClips] = useState<ClipMeta[]>([])
  const [loading, setLoading] = useState(false)
  const [selectedClipId, setSelectedClipId] = useState('')
  const [selectedChannel, setSelectedChannel] = useState(defaultChannel ?? '')
  const [date, setDate] = useState('')
  const [time, setTime] = useState('12:00')
  const [submitting, setSubmitting] = useState(false)

  const channelEntries = Object.entries(channels ?? {})

  useEffect(() => {
    if (!open) return
    setLoading(true)
    setSelectedClipId('')
    setSelectedChannel(defaultChannel ?? (channelEntries.length === 1 ? channelEntries[0][0] : ''))

    // Default to tomorrow
    const tomorrow = new Date()
    tomorrow.setDate(tomorrow.getDate() + 1)
    setDate(tomorrow.toISOString().slice(0, 10))
    setTime('12:00')

    fetchStudioClips({ sort: 'recent', limit: 100 })
      .then((data) => {
        // Only show clips that haven't been uploaded yet (or all if needed)
        setClips(data)
      })
      .catch(() => toast.error('Failed to load clips'))
      .finally(() => setLoading(false))
  }, [open])

  async function handleSubmit() {
    if (!selectedClipId || !selectedChannel || !date || !time) return
    setSubmitting(true)
    try {
      const scheduledAt = new Date(`${date}T${time}:00`).toISOString()
      await scheduleRelease(selectedClipId, selectedChannel, scheduledAt)
      toast.success('Release scheduled')
      onScheduled()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Schedule failed')
    } finally {
      setSubmitting(false)
    }
  }

  const readyClips = clips.filter((c) => !c.video_id)
  const uploadedClips = clips.filter((c) => !!c.video_id)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Schedule Release</DialogTitle>
        </DialogHeader>

        {loading ? (
          <div className="flex justify-center py-8">
            <Loader2 className="size-6 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <div className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Clip</label>
              <Select value={selectedClipId} onValueChange={setSelectedClipId}>
                <SelectTrigger>
                  <SelectValue placeholder="Select a clip..." />
                </SelectTrigger>
                <SelectContent>
                  {readyClips.length > 0 && (
                    <>
                      <div className="px-2 py-1 text-[10px] uppercase tracking-wider text-muted-foreground">
                        Ready to upload
                      </div>
                      {readyClips.map((c) => (
                        <SelectItem key={c.id} value={c.id}>
                          {c._title_override ?? c.title}
                        </SelectItem>
                      ))}
                    </>
                  )}
                  {uploadedClips.length > 0 && (
                    <>
                      <div className="px-2 py-1 text-[10px] uppercase tracking-wider text-muted-foreground">
                        Already uploaded
                      </div>
                      {uploadedClips.map((c) => (
                        <SelectItem key={c.id} value={c.id}>
                          {c._title_override ?? c.title}
                        </SelectItem>
                      ))}
                    </>
                  )}
                  {clips.length === 0 && (
                    <div className="px-2 py-2 text-sm text-muted-foreground">
                      No output clips available
                    </div>
                  )}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <label className="text-sm font-medium">Channel</label>
              {channelEntries.length === 0 ? (
                <p className="text-sm text-muted-foreground">No channels configured in config.yaml</p>
              ) : (
                <Select value={selectedChannel} onValueChange={setSelectedChannel}>
                  <SelectTrigger>
                    <SelectValue placeholder="Select channel..." />
                  </SelectTrigger>
                  <SelectContent>
                    {channelEntries.map(([key, info]) => (
                      <SelectItem key={key} value={key}>
                        {info?.name ?? key}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <label className="text-sm font-medium">Date</label>
                <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <label className="text-sm font-medium">Time</label>
                <Input type="time" value={time} onChange={(e) => setTime(e.target.value)} />
              </div>
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            onClick={handleSubmit}
            disabled={submitting || !selectedClipId || !selectedChannel || !date || !time}
          >
            {submitting && <Loader2 className="size-4 animate-spin mr-1" />}
            Schedule
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
