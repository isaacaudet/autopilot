import { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'
import { ExternalLink } from 'lucide-react'
import { fetchAnalytics } from '@/lib/api'

interface AnalyticsVideo {
  video_id: string
  title: string
  published_at: string
  views: number
  likes: number
  comments: number
}

function viewColor(views: number): string {
  if (views >= 1000) return 'text-green-400'
  if (views >= 100) return 'text-yellow-400'
  return 'text-muted-foreground'
}

export function AnalyticsPage() {
  const [videos, setVideos] = useState<AnalyticsVideo[]>([])

  useEffect(() => {
    fetchAnalytics().then((data) => setVideos(data as AnalyticsVideo[])).catch(() => {})
  }, [])

  const sorted = [...videos].sort((a, b) => b.views - a.views)
  const totalViews = videos.reduce((s, v) => s + v.views, 0)
  const totalLikes = videos.reduce((s, v) => s + v.likes, 0)
  const avgViews = videos.length ? Math.round(totalViews / videos.length) : 0

  if (videos.length === 0) {
    return (
      <div className="space-y-5">
        <h1 className="text-lg font-semibold tracking-tight">Analytics</h1>
        <div className="rounded-lg border border-dashed p-8 text-center">
          <p className="text-sm text-muted-foreground">No analytics data available.</p>
          <p className="text-xs text-muted-foreground mt-2">
            Ensure YouTube OAuth is configured. Run: clipper analytics --days 90
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-5">
      <h1 className="text-lg font-semibold tracking-tight">Analytics</h1>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium uppercase tracking-wider text-muted-foreground">Total Views</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-xl font-semibold">{totalViews.toLocaleString()}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium uppercase tracking-wider text-muted-foreground">Total Likes</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-xl font-semibold">{totalLikes.toLocaleString()}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium uppercase tracking-wider text-muted-foreground">Avg Views/Video</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-xl font-semibold">{avgViews.toLocaleString()}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium uppercase tracking-wider text-muted-foreground">Video Count</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-xl font-semibold">{videos.length}</p>
          </CardContent>
        </Card>
      </div>

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Title</TableHead>
              <TableHead>Published</TableHead>
              <TableHead className="text-right">Views</TableHead>
              <TableHead className="text-right">Likes</TableHead>
              <TableHead className="text-right">Comments</TableHead>
              <TableHead className="text-right">Link</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((v) => (
              <TableRow key={v.video_id}>
                <TableCell className="max-w-xs truncate">{v.title}</TableCell>
                <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                  {new Date(v.published_at).toLocaleDateString()}
                </TableCell>
                <TableCell className={`text-right font-mono ${viewColor(v.views)}`}>
                  {v.views.toLocaleString()}
                </TableCell>
                <TableCell className="text-right font-mono">{v.likes.toLocaleString()}</TableCell>
                <TableCell className="text-right font-mono">{v.comments.toLocaleString()}</TableCell>
                <TableCell className="text-right">
                  <a
                    href={`https://youtube.com/watch?v=${v.video_id}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center justify-center size-8 rounded-md hover:bg-accent"
                  >
                    <ExternalLink className="size-4 text-primary" />
                  </a>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
