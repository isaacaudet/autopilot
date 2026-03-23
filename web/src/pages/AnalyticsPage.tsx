import { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, BarChart, Bar,
} from 'recharts'

const CHANNEL_COLORS: Record<string, string> = {
  pro_deadlock:      '#06b6d4', // cyan
  pro_marathon:      '#a855f7', // purple
  instagram_main:    '#f97316', // orange
  instagram_marathon:'#ec4899', // pink
}

const CHANNEL_LABELS: Record<string, string> = {
  pro_deadlock:      'YT · Pro Deadlock',
  pro_marathon:      'YT · Pro Marathon',
  instagram_main:    'IG · Main',
  instagram_marathon:'IG · Marathon',
}

interface DayRow { date: string; views: number; likes?: number; shares?: number }
interface ChannelData {
  platform: string
  label: string
  metric?: string  // 'reach' for Instagram, undefined/'views' for YouTube
  daily: DayRow[]
  total_views: number
}
interface DailyResponse {
  days: number
  channels: Record<string, ChannelData>
  errors: string[]
}

function fmtK(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

function fmtDate(d: string) {
  const [, m, day] = d.split('-')
  return `${parseInt(m)}/${parseInt(day)}`
}

/** Merge multiple channel daily arrays into a single [{date, ch1, ch2, ...}] */
function mergeDaily(channels: Record<string, ChannelData>, keys: string[]): Record<string, number | string>[] {
  const map: Record<string, Record<string, number>> = {}
  for (const key of keys) {
    for (const row of (channels[key]?.daily ?? [])) {
      if (!map[row.date]) map[row.date] = {}
      map[row.date][key] = row.views
    }
  }
  return Object.entries(map)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, vals]) => ({ date: fmtDate(date), ...vals }))
}

export function AnalyticsPage() {
  const [data, setData] = useState<DailyResponse | null>(null)
  const [window, setWindow] = useState('30')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    fetch(`/api/analytics/daily?days=${window}`)
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [window])

  const channels = data?.channels ?? {}
  const allKeys = Object.keys(channels)
  const ytKeys = allKeys.filter(k => channels[k].platform === 'youtube')
  const igKeys = allKeys.filter(k => channels[k].platform === 'instagram')

  const ytMerged = mergeDaily(channels, ytKeys)
  const igMerged = mergeDaily(channels, igKeys)
  const allMerged = mergeDaily(channels, allKeys)

  const totalViews = allKeys.reduce((s, k) => s + (channels[k]?.total_views ?? 0), 0)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold tracking-tight">Analytics</h1>
        <Tabs value={window} onValueChange={setWindow}>
          <TabsList>
            <TabsTrigger value="7">7d</TabsTrigger>
            <TabsTrigger value="14">14d</TabsTrigger>
            <TabsTrigger value="30">30d</TabsTrigger>
            <TabsTrigger value="90">90d</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      {/* Summary stat cards */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Card>
          <CardHeader className="pb-1 pt-3 px-4">
            <CardTitle className="text-xs uppercase tracking-wider text-muted-foreground">Total Views</CardTitle>
          </CardHeader>
          <CardContent className="px-4 pb-3">
            <p className="text-2xl font-bold">{fmtK(totalViews)}</p>
            <p className="text-xs text-muted-foreground">all platforms</p>
          </CardContent>
        </Card>
        {allKeys.map(key => (
          <Card key={key}>
            <CardHeader className="pb-1 pt-3 px-4">
              <CardTitle className="text-xs uppercase tracking-wider" style={{ color: CHANNEL_COLORS[key] }}>
                {CHANNEL_LABELS[key] ?? channels[key].label}
              </CardTitle>
            </CardHeader>
            <CardContent className="px-4 pb-3">
              <p className="text-2xl font-bold">{fmtK(channels[key].total_views)}</p>
              <p className="text-xs text-muted-foreground">{window}d {channels[key].metric === 'reach' ? 'reach' : 'views'}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      {loading && <p className="text-sm text-muted-foreground">Loading…</p>}

      {/* All platforms combined */}
      {allMerged.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">All Platforms — Daily Views</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={allMerged} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#333" />
                <XAxis dataKey="date" tick={{ fontSize: 11, fill: '#888' }} />
                <YAxis tickFormatter={fmtK} tick={{ fontSize: 11, fill: '#888' }} width={40} />
                <Tooltip
                  contentStyle={{ background: '#1a1a1a', border: '1px solid #333', fontSize: 12 }}
                  formatter={(v: number) => fmtK(v)}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                {allKeys.map(key => (
                  <Line
                    key={key}
                    type="monotone"
                    dataKey={key}
                    name={CHANNEL_LABELS[key] ?? key}
                    stroke={CHANNEL_COLORS[key] ?? '#888'}
                    dot={false}
                    strokeWidth={2}
                    connectNulls
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      )}

      {/* YouTube channels */}
      {ytMerged.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">YouTube — Daily Views by Channel</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={ytMerged} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#333" />
                <XAxis dataKey="date" tick={{ fontSize: 11, fill: '#888' }} />
                <YAxis tickFormatter={fmtK} tick={{ fontSize: 11, fill: '#888' }} width={40} />
                <Tooltip
                  contentStyle={{ background: '#1a1a1a', border: '1px solid #333', fontSize: 12 }}
                  formatter={(v: number) => fmtK(v)}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                {ytKeys.map(key => (
                  <Bar key={key} dataKey={key} name={CHANNEL_LABELS[key] ?? key}
                    fill={CHANNEL_COLORS[key] ?? '#888'} stackId="yt" />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      )}

      {/* Instagram channels */}
      {igMerged.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Instagram — Daily Reach by Account</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={igMerged} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#333" />
                <XAxis dataKey="date" tick={{ fontSize: 11, fill: '#888' }} />
                <YAxis tickFormatter={fmtK} tick={{ fontSize: 11, fill: '#888' }} width={40} />
                <Tooltip
                  contentStyle={{ background: '#1a1a1a', border: '1px solid #333', fontSize: 12 }}
                  formatter={(v: number) => fmtK(v)}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                {igKeys.map(key => (
                  <Bar key={key} dataKey={key} name={CHANNEL_LABELS[key] ?? key}
                    fill={CHANNEL_COLORS[key] ?? '#888'} stackId="ig" />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      )}

      {data?.errors?.length ? (
        <p className="text-xs text-muted-foreground">
          Errors: {data.errors.join(' · ')}
        </p>
      ) : null}
    </div>
  )
}
