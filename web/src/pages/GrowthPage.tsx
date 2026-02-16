import { useEffect, useMemo, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { fetchGrowthScoreboard, type GrowthScoreboard, type GrowthSegmentRow } from '@/lib/api'
import { ExternalLink, TrendingUp, Clock3, Scissors, Rocket } from 'lucide-react'
import { useChannelScope } from '@/hooks/useChannelScope'

const windows = [30, 60, 90, 180]

function SegmentTable({ rows }: { rows: GrowthSegmentRow[] }) {
  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground">No segment data available yet.</p>
  }

  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Segment</TableHead>
            <TableHead className="text-right">Uploads</TableHead>
            <TableHead className="text-right">Avg Views</TableHead>
            <TableHead className="text-right">Median</TableHead>
            <TableHead className="text-right">Win Rate</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.slice(0, 12).map((row) => (
            <TableRow key={row.name}>
              <TableCell className="font-medium">{row.name}</TableCell>
              <TableCell className="text-right tabular-nums">{row.uploads}</TableCell>
              <TableCell className="text-right tabular-nums">{Math.round(row.avg_views).toLocaleString()}</TableCell>
              <TableCell className="text-right tabular-nums">{Math.round(row.median_views).toLocaleString()}</TableCell>
              <TableCell className="text-right tabular-nums">{(row.win_rate * 100).toFixed(0)}%</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

export function GrowthPage() {
  const [days, setDays] = useState(90)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [data, setData] = useState<GrowthScoreboard | null>(null)
  const { channel, channels } = useChannelScope()

  async function load(windowDays: number, opts?: { refresh?: boolean }) {
    setLoading(true)
    setError('')
    try {
      const next = await fetchGrowthScoreboard(windowDays, {
        ...opts,
        channel,
      })
      setData(next)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Growth fetch failed')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load(days)
  }, [days, channel])

  const summary = useMemo(
    () =>
      data?.summary ?? {
        uploads: 0,
        total_views: 0,
        avg_views: 0,
        median_views: 0,
        winner_cutoff: 0,
      },
    [data],
  )

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold tracking-tight">Growth</h1>
          <p className="text-sm text-muted-foreground">
            Track what to scale, what to kill, and when to post next.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select value={String(days)} onValueChange={(v) => setDays(Number(v))}>
            <SelectTrigger className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {windows.map((w) => (
                <SelectItem key={w} value={String(w)}>
                  Last {w} days
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button variant="outline" onClick={() => load(days, { refresh: true })} disabled={loading}>
            Refresh
          </Button>
        </div>
      </div>

      {error && (
        <Card className="border-destructive/40">
          <CardContent className="pt-4 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-xs text-muted-foreground">Uploads</CardTitle></CardHeader>
          <CardContent><p className="text-3xl tabular-nums font-semibold">{summary.uploads}</p></CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-xs text-muted-foreground">Total Views</CardTitle></CardHeader>
          <CardContent><p className="text-3xl tabular-nums font-semibold">{summary.total_views.toLocaleString()}</p></CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-xs text-muted-foreground">Avg Views</CardTitle></CardHeader>
          <CardContent><p className="text-3xl tabular-nums font-semibold">{Math.round(summary.avg_views).toLocaleString()}</p></CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-xs text-muted-foreground">Median Views</CardTitle></CardHeader>
          <CardContent><p className="text-3xl tabular-nums font-semibold">{Math.round(summary.median_views).toLocaleString()}</p></CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-xs text-muted-foreground">Winner Cutoff</CardTitle></CardHeader>
          <CardContent><p className="text-3xl tabular-nums font-semibold">{Math.round(summary.winner_cutoff).toLocaleString()}</p></CardContent>
        </Card>
      </div>

      {data && (
        <div className="grid gap-4 xl:grid-cols-2">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <Clock3 className="size-4 text-primary" />
                Best Posting Windows
              </CardTitle>
            </CardHeader>
            <CardContent>
              {data.posting_times.length === 0 ? (
                <p className="text-sm text-muted-foreground">Need more historical uploads to rank posting windows.</p>
              ) : (
                <div className="space-y-2">
                  {data.posting_times.slice(0, 6).map((slot, i) => (
                    <div key={`${slot.weekday}-${slot.hour}`} className="rounded-lg border p-3 flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium">{i + 1}. {slot.weekday} {slot.hour}</p>
                        <p className="text-xs text-muted-foreground">
                          {slot.uploads} uploads · {Math.round(slot.avg_views).toLocaleString()} avg views
                        </p>
                      </div>
                      <Badge variant="secondary">{(slot.win_rate * 100).toFixed(0)}% win</Badge>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">2h Kill / Scale Guardrails</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid grid-cols-2 gap-2 text-sm">
                <div className="rounded-lg border p-3">
                  <p className="text-xs text-muted-foreground flex items-center gap-1"><Scissors className="size-3" />Kill if under</p>
                  <p className="text-xl tabular-nums font-semibold">{Math.round(data.kill_scale?.kill_threshold ?? 0)}</p>
                </div>
                <div className="rounded-lg border p-3">
                  <p className="text-xs text-muted-foreground flex items-center gap-1"><Rocket className="size-3" />Scale if over</p>
                  <p className="text-xl tabular-nums font-semibold">{Math.round(data.kill_scale?.scale_threshold ?? 0)}</p>
                </div>
              </div>

              {(data.kill_scale?.actions?.length ?? 0) === 0 ? (
                <p className="text-sm text-muted-foreground">No uploads in the last 2 hours to classify yet.</p>
              ) : (
                <div className="space-y-2">
                  {data.kill_scale?.actions.slice(0, 6).map((row) => (
                    <div key={row.video_id} className="rounded-lg border p-3">
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-sm font-medium line-clamp-1">{row.title}</p>
                        <Badge
                          variant={row.action === 'scale' ? 'default' : row.action === 'kill' ? 'destructive' : 'secondary'}
                          className="uppercase"
                        >
                          {row.action}
                        </Badge>
                      </div>
                      <p className="text-xs text-muted-foreground mt-1">
                        {row.views.toLocaleString()} views in {row.age_hours.toFixed(1)}h
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {data && data.notes.length > 0 && (
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm">Signals</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            {data.notes.map((note, i) => (
              <div key={i} className="flex items-start gap-2 text-sm">
                <TrendingUp className="mt-0.5 size-4 text-primary" />
                <p>{note}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader className="pb-2"><CardTitle className="text-sm">Top Videos</CardTitle></CardHeader>
        <CardContent>
          {!data || data.top_videos.length === 0 ? (
            <p className="text-sm text-muted-foreground">No video performance rows available.</p>
          ) : (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Title</TableHead>
                    <TableHead>Channel</TableHead>
                    <TableHead>Segment</TableHead>
                    <TableHead className="text-right">Views</TableHead>
                    <TableHead className="text-right">Likes</TableHead>
                    <TableHead className="text-right">Link</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.top_videos.slice(0, 12).map((video) => (
                    <TableRow key={video.video_id}>
                      <TableCell className="max-w-sm truncate">{video.title}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {video.channel && channels?.[video.channel]?.name ? (
                          channels[video.channel].name
                        ) : (
                          video.channel || '—'
                        )}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {video.game} · {video.streamer}
                      </TableCell>
                      <TableCell className="text-right tabular-nums font-medium">{video.views.toLocaleString()}</TableCell>
                      <TableCell className="text-right tabular-nums">{video.likes.toLocaleString()}</TableCell>
                      <TableCell className="text-right">
                        <a
                          href={`https://youtube.com/watch?v=${video.video_id}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center justify-center rounded-md p-1.5 hover:bg-accent"
                        >
                          <ExternalLink className="size-4 text-primary" />
                        </a>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm">Segment Breakdown</CardTitle>
            {data && <Badge variant="secondary">Generated {new Date(data.generated_at).toLocaleString()}</Badge>}
          </div>
        </CardHeader>
        <CardContent>
          {!data ? (
            <p className="text-sm text-muted-foreground">No segment data available.</p>
          ) : (
            <Tabs defaultValue="game">
              <TabsList>
                <TabsTrigger value="game">Game</TabsTrigger>
                <TabsTrigger value="streamer">Streamer</TabsTrigger>
                <TabsTrigger value="category">Category</TabsTrigger>
                <TabsTrigger value="hour">Publish Hour</TabsTrigger>
              </TabsList>
              <TabsContent value="game"><SegmentTable rows={data.by_game} /></TabsContent>
              <TabsContent value="streamer"><SegmentTable rows={data.by_streamer} /></TabsContent>
              <TabsContent value="category"><SegmentTable rows={data.by_category} /></TabsContent>
              <TabsContent value="hour"><SegmentTable rows={data.by_hour} /></TabsContent>
            </Tabs>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
