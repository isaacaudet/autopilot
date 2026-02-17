import type { ClipMeta, ConfigData, FetchScoreResponse, Release } from './types'

const BASE = ''

export async function fetchConfig(): Promise<ConfigData> {
  const res = await fetch(`${BASE}/api/config`)
  return res.json()
}

export async function fetchQueue(
  status: 'pending' | 'approved' | 'output',
  opts?: {
    game?: string
    streamer?: string
    sort?: string
    includeOrphans?: boolean
    limit?: number
    channel?: string
  }
): Promise<ClipMeta[]> {
  const params = new URLSearchParams({ status })
  if (opts?.game) params.set('game', opts.game)
  if (opts?.streamer) params.set('streamer', opts.streamer)
  if (opts?.channel && opts.channel !== 'all') params.set('channel', opts.channel)
  if (opts?.sort) params.set('sort', opts.sort)
  if (opts?.includeOrphans === false) params.set('include_orphans', 'false')
  if (opts?.limit != null) params.set('limit', String(opts.limit))
  const res = await fetch(`${BASE}/api/queue?${params}`)
  const data = await res.json()
  return data.clips
}

export async function resyncOutput(limit = 200): Promise<{ created: number; clips: string[]; orphans_found: number }> {
  const res = await fetch(`${BASE}/api/output/resync?limit=${encodeURIComponent(String(limit))}`, {
    method: 'POST',
  })
  if (!res.ok) throw new Error(`Output reindex failed: ${res.status}`)
  return res.json()
}

export async function openOutputFolder(): Promise<void> {
  const res = await fetch(`${BASE}/api/output/open-folder`, { method: 'POST' })
  if (!res.ok) throw new Error(`Open output folder failed: ${res.status}`)
}

export async function openOutputClip(clipId: string): Promise<void> {
  const res = await fetch(`${BASE}/api/output/open/${encodeURIComponent(clipId)}`, { method: 'POST' })
  if (!res.ok) throw new Error(`Reveal output clip failed: ${res.status}`)
}

export function videoUrl(clipId: string): string {
  return `${BASE}/api/video/${encodeURIComponent(clipId)}`
}

export async function startWorkflow(params: {
  recipe: 'shorts' | 'compilation'
  game: string
  count: number
  channel: string | null
}): Promise<void> {
  await fetch(`${BASE}/api/workflow/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
}

export async function fetchScore(
  game: string,
  channel?: string | null,
  opts?: {
    period?: string
    fetchScope?: 'gamewide' | 'configured' | 'selected'
    streamers?: string[]
  },
): Promise<FetchScoreResponse> {
  const payload = {
    game,
    channel: channel ?? null,
    period: opts?.period ?? '24h',
    fetch_scope: opts?.fetchScope ?? 'gamewide',
    streamers: opts?.streamers ?? [],
  }
  const res = await fetch(`${BASE}/api/workflow/fetch-score`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error(`Fetch score failed: ${res.status}`)
  return res.json()
}

export async function approveProcess(
  clipIds: string[],
  recipe: string,
  channel?: string | null,
  shortsLayout?: string | null,
  layoutOverrides?: Record<string, unknown> | null,
): Promise<void> {
  const res = await fetch(`${BASE}/api/workflow/approve-process`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      clip_ids: clipIds,
      recipe,
      channel: channel ?? null,
      shorts_layout: shortsLayout ?? null,
      layout_overrides: layoutOverrides ?? null,
    }),
  })
  if (!res.ok) throw new Error(`Approve failed: ${res.status}`)
}

export async function uploadClip(
  clipId: string,
  privacy = 'unlisted',
  channel?: string | null,
): Promise<{ video_id: string | null; channel: string | null; platform?: string }> {
  const res = await fetch(`${BASE}/api/upload`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ clip_id: clipId, privacy, channel: channel ?? null }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `Upload failed: ${res.status}` }))
    throw new Error(err.detail || `Upload failed: ${res.status}`)
  }
  const data = await res.json()
  return { video_id: data.video_id ?? null, channel: data.channel ?? null, platform: data.platform }
}

export async function uploadBatch(
  clipIds: string[],
  privacy = 'unlisted',
  channel?: string | null,
): Promise<void> {
  const res = await fetch(`${BASE}/api/upload/batch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ clip_ids: clipIds, privacy, channel: channel ?? null }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `Batch upload failed: ${res.status}` }))
    throw new Error(err.detail || `Batch upload failed: ${res.status}`)
  }
}

export async function fetchReleases(channel?: string): Promise<Release[]> {
  const params = new URLSearchParams()
  if (channel && channel !== 'all') params.set('channel', channel)
  const url = params.toString() ? `${BASE}/api/releases?${params}` : `${BASE}/api/releases`
  const res = await fetch(url)
  const data = await res.json()
  return data.releases
}

export async function scheduleRelease(clipId: string, channel: string, scheduledAt: string): Promise<void> {
  await fetch(`${BASE}/api/releases`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ clip_id: clipId, channel, scheduled_at: scheduledAt }),
  })
}

export async function fetchAnalytics(): Promise<unknown[]> {
  const res = await fetch(`${BASE}/api/analytics`)
  const data = await res.json()
  return data.videos
}

export async function updateClip(
  clipId: string,
  data: { title_override?: string; description_override?: string },
): Promise<void> {
  await fetch(`${BASE}/api/clips/${encodeURIComponent(clipId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export async function updateClipMetadata(
  clipId: string,
  data: {
    title_override?: string
    description_override?: string
    tags_override?: string[]
    sync_youtube?: boolean
    hook_text_override?: string
    hook_duration?: number
  },
): Promise<void> {
  const res = await fetch(`${BASE}/api/clips/${encodeURIComponent(clipId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(err.detail || `Update failed: ${res.status}`)
  }
}

export async function getTitlePreview(clipId: string): Promise<string> {
  const res = await fetch(`${BASE}/api/clips/${encodeURIComponent(clipId)}/title-preview`)
  const data = await res.json()
  return data.title
}

export async function publishVideos(videoIds: string[]): Promise<void> {
  await fetch(`${BASE}/api/publish`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ video_ids: videoIds }),
  })
}

export async function publishClip(clipId: string): Promise<{ video_id: string; published: boolean; channel: string | null }> {
  const res = await fetch(`${BASE}/api/clips/${encodeURIComponent(clipId)}/publish`, { method: 'POST' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `Publish failed: ${res.status}` }))
    throw new Error(err.detail || `Publish failed: ${res.status}`)
  }
  return res.json()
}

export async function fetchCompilations(channel?: string): Promise<ClipMeta[]> {
  const params = new URLSearchParams()
  if (channel && channel !== 'all') params.set('channel', channel)
  const url = params.toString() ? `${BASE}/api/compilations?${params}` : `${BASE}/api/compilations`
  const res = await fetch(url)
  const data = await res.json()
  return data.compilations
}

export async function fetchCompilationClips(channel?: string): Promise<ClipMeta[]> {
  const params = new URLSearchParams()
  if (channel && channel !== 'all') params.set('channel', channel)
  const url = params.toString() ? `${BASE}/api/compilation/clips?${params}` : `${BASE}/api/compilation/clips`
  const res = await fetch(url)
  const data = await res.json()
  return data.clips
}

export async function buildCompilation(
  clipIds: string[],
  title?: string,
  countdown = true,
  channel?: string | null,
): Promise<void> {
  await fetch(`${BASE}/api/compilation/build`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ clip_ids: clipIds, title: title || null, countdown, channel: channel ?? null }),
  })
}

export function thumbnailUrl(clipId: string): string {
  return `${BASE}/api/clips/${encodeURIComponent(clipId)}/thumbnail`
}

export interface FacecamRect {
  x: number
  y: number
  w: number
  h: number
}

export interface LayoutProfile {
  facecam?: FacecamRect
  hud?: FacecamRect
  // If false, treat as "no facecam present" and degrade to Classic layout for this streamer.
  facecam_enabled?: boolean
  // If false, skip HUD overlay for this streamer.
  hud_enabled?: boolean
  // Per-streamer fill layout tuning.
  safe_top_ratio?: number
  safe_bottom_ratio?: number
  facecam_band_ratio?: number
  facecam_x_bias?: number
  gameplay_zoom?: number
  gameplay_zoom_no_facecam?: number
  gameplay_x_bias?: number
  gameplay_y_bias?: number
  hud_height_ratio?: number
  hud_scale?: number
  hud_x_ratio?: number
  hud_y_ratio?: number
  title_y_ratio?: number
  subtitle_margin_ratio?: number
  facecam_y_bias?: number
  facecam_zoom?: number
}

export async function fetchLayoutProfiles(): Promise<Record<string, LayoutProfile>> {
  const res = await fetch(`${BASE}/api/layout/facecam-profiles`)
  if (!res.ok) throw new Error(`Facecam profiles fetch failed: ${res.status}`)
  const data = await res.json()
  return (data.profiles ?? {}) as Record<string, LayoutProfile>
}

export async function saveLayoutProfile(streamer: string, profile: LayoutProfile): Promise<void> {
  const res = await fetch(`${BASE}/api/layout/facecam-profiles/${encodeURIComponent(streamer)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(profile),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `Save failed: ${res.status}` }))
    throw new Error(err.detail || `Save failed: ${res.status}`)
  }
}

export async function removeLayoutProfile(streamer: string): Promise<void> {
  const res = await fetch(`${BASE}/api/layout/facecam-profiles/${encodeURIComponent(streamer)}`, {
    method: 'DELETE',
  })
  if (!res.ok) throw new Error(`Delete failed: ${res.status}`)
}

export async function regenerateThumbnail(clipId: string): Promise<void> {
  await fetch(`${BASE}/api/clips/${encodeURIComponent(clipId)}/thumbnail/regenerate`, {
    method: 'POST',
  })
}

export async function getTagsPreview(clipId: string): Promise<string[]> {
  const res = await fetch(`${BASE}/api/clips/${encodeURIComponent(clipId)}/tags-preview`)
  const data = await res.json()
  return data.tags
}

export async function getDescriptionPreview(clipId: string): Promise<string> {
  const res = await fetch(`${BASE}/api/clips/${encodeURIComponent(clipId)}/description-preview`)
  const data = await res.json()
  return data.description
}

// -- Discover & Autopilot endpoints --

export interface TrendingGame {
  game_id: string
  game_name: string
  platform: string
  clip_count: number
  total_views: number
  avg_views: number
}

export interface TrendingResponse {
  games: TrendingGame[]
  degraded?: boolean
  source?: string
  detail?: string
}

export interface GapRow {
  game_name: string
  twitch_clips: number
  twitch_views: number
  yt_videos: number
  yt_views: number
  gap_score: number
  opportunity: 'high' | 'medium' | 'low'
}

export interface GapResponse {
  gaps: GapRow[]
  degraded: boolean
  youtube_available: boolean
  detail?: string | null
}

export interface GrowthSegmentRow {
  name: string
  uploads: number
  total_views: number
  avg_views: number
  median_views: number
  winner_cutoff: number
  win_rate: number
  avg_subtitle_qa?: number | null
}

export interface GrowthVideoRow {
  video_id: string
  title: string
  channel?: string
  published_at: string
  views: number
  likes: number
  comments: number
  game: string
  streamer: string
  category: string
  subtitle_qa_score?: number | null
}

export interface GrowthPostingTime {
  weekday: string
  hour: string
  uploads: number
  avg_views: number
  win_rate: number
  score: number
}

export interface GrowthKillScaleAction {
  video_id: string
  title: string
  published_at: string
  age_hours: number
  views: number
  action: 'kill' | 'hold' | 'scale'
  reason: string
}

export interface GrowthScoreboard {
  generated_at: string
  days: number
  channel?: string
  summary: {
    uploads: number
    total_views: number
    avg_views: number
    median_views: number
    winner_cutoff: number
  }
  top_videos: GrowthVideoRow[]
  by_game: GrowthSegmentRow[]
  by_streamer: GrowthSegmentRow[]
  by_category: GrowthSegmentRow[]
  by_hour: GrowthSegmentRow[]
  posting_times: GrowthPostingTime[]
  kill_scale?: {
    hours_window: number
    kill_threshold: number
    scale_threshold: number
    actions: GrowthKillScaleAction[]
  }
  notes: string[]
}

export async function fetchTrending(): Promise<TrendingResponse> {
  const res = await fetch(`${BASE}/api/discover/trending`)
  if (!res.ok) throw new Error(`Trending fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchGaps(limit = 20): Promise<GapResponse> {
  const res = await fetch(`${BASE}/api/discover/gaps?limit=${encodeURIComponent(String(limit))}`)
  if (!res.ok) throw new Error(`Gaps fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchGrowthScoreboard(
  days = 90,
  opts?: {
    refresh?: boolean
    channel?: string
  },
): Promise<GrowthScoreboard> {
  const params = new URLSearchParams({ days: String(days) })
  if (opts?.refresh) params.set('refresh', 'true')
  if (opts?.channel) params.set('channel', opts.channel)
  const res = await fetch(`${BASE}/api/growth/scoreboard?${params}`)
  if (!res.ok) throw new Error(`Growth fetch failed: ${res.status}`)
  return res.json()
}

export async function startAutopilot(opts?: {
  count?: number
  min_score?: number
  channel?: string | null
}): Promise<{ status: string }> {
  const res = await fetch(`${BASE}/api/autopilot/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      count: opts?.count ?? 5,
      min_score: opts?.min_score ?? 40,
      channel: opts?.channel ?? null,
    }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(err.detail || `Autopilot failed: ${res.status}`)
  }
  return res.json()
}

// -- Learning endpoints --

export async function collectPerformance(): Promise<{ collected: number }> {
  const res = await fetch(`${BASE}/api/learn/collect`, { method: 'POST' })
  if (!res.ok) throw new Error(`Collect failed: ${res.status}`)
  return res.json()
}

export async function trainWeights(): Promise<Record<string, unknown>> {
  const res = await fetch(`${BASE}/api/learn/train`, { method: 'POST' })
  if (!res.ok) throw new Error(`Train failed: ${res.status}`)
  return res.json()
}

export async function fetchLearnStatus(): Promise<{
  learned: boolean
  sample_count: number
  weights: Record<string, number> | null
}> {
  const res = await fetch(`${BASE}/api/learn/status`)
  if (!res.ok) throw new Error(`Learn status failed: ${res.status}`)
  return res.json()
}

export async function batchReview(
  clipIds: string[],
  action: 'approve' | 'skip',
): Promise<{ updated: number; status: string }> {
  const res = await fetch(`${BASE}/api/review/batch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ clip_ids: clipIds, action }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `Batch review failed: ${res.status}` }))
    throw new Error(err.detail || `Batch review failed: ${res.status}`)
  }
  return res.json()
}

// -- Subtitle endpoints --

export interface SubtitleLine {
  index: number
  start: string    // "0:00:01.50"
  end: string      // "0:00:03.20"
  text: string     // plain text (tags stripped)
  raw: string      // original ASS text with tags (for write-back)
}

export async function fetchSubtitles(clipId: string): Promise<SubtitleLine[]> {
  const res = await fetch(`${BASE}/api/clips/${encodeURIComponent(clipId)}/subtitles`)
  if (!res.ok) {
    if (res.status === 404) return []
    throw new Error(`Fetch subtitles failed: ${res.status}`)
  }
  const data = await res.json()
  return data.lines
}

export async function reburnSubtitles(clipId: string): Promise<void> {
  const res = await fetch(`${BASE}/api/clips/${encodeURIComponent(clipId)}/reburn`, { method: 'POST' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `Re-burn failed: ${res.status}` }))
    throw new Error(err.detail || `Re-burn failed: ${res.status}`)
  }
}

export async function updateSubtitles(clipId: string, lines: SubtitleLine[]): Promise<void> {
  const res = await fetch(`${BASE}/api/clips/${encodeURIComponent(clipId)}/subtitles`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lines }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `Save subtitles failed: ${res.status}` }))
    throw new Error(err.detail || `Save subtitles failed: ${res.status}`)
  }
}

export async function fetchStudioClips(opts?: {
  game?: string
  sort?: string
  limit?: number
  channel?: string
}): Promise<ClipMeta[]> {
  const params = new URLSearchParams({
    status: 'output',
    include_compilations: 'true',
    include_orphans: 'true',
    sort: opts?.sort || 'recent',
    limit: String(opts?.limit ?? 250),
  })
  if (opts?.game) params.set('game', opts.game)
  if (opts?.channel && opts.channel !== 'all') params.set('channel', opts.channel)
  const res = await fetch(`${BASE}/api/queue?${params}`)
  const data = await res.json()
  return data.clips
}
