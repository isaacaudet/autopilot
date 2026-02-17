export interface ClipMeta {
  id: string
  title: string
  streamer: string
  game: string
  platform: string
  url: string
  duration: number
  view_count: number
  _score?: number
  processed_path?: string
  video_id?: string
  tiktok_id?: string
  instagram_id?: string
  facebook_id?: string
  channel?: string
  _target_channel?: string
  is_shorts?: boolean
  clip_count?: number | null
  _title_override?: string
  _description_override?: string
  _tags_override?: string[]
  _generated_title?: string
  _generated_description?: string
  _generated_tags?: string[]
  _orphan?: boolean
  _subtitle_path?: string
  _hook_text_override?: string
  _hook_duration?: number
  _analysis?: { category?: string; title_variants?: string[]; hook_text?: string; [key: string]: unknown }
  thumbnail_url?: string
  created_at?: string
}

export interface PipelineSnapshot {
  total: number
  completed: number
  failed: number
  workers: Record<string, [string, string, number]>
  completed_clips: string[]
  completed_clip_ids: string[]
  errors: string[]
  elapsed: number
  eta: number | null
  running?: boolean
  compile_step?: string
  compile_progress?: number
  uploads_done?: number
  uploads_total?: number
  recipe?: string
  phase?: string
  phase_detail?: string
}

export interface Tier {
  target_min: number
  label: string
  count: number
  avg_score: number
  actual_min: number
  quality: 'excellent' | 'good' | 'decent'
}

export interface FetchScoreResponse {
  clips: ClipMeta[]
  tiers: Tier[]
  clip_count: number
}

export interface Release {
  clip_id: string
  channel: string
  scheduled_at: string
  status: 'pending' | 'uploaded' | 'published' | 'failed'
  video_id: string | null
  meta_path: string | null
  _path?: string
}

export interface ConfigData {
  targets: {
    twitch: { streamers: string[]; games: string[]; clips_per_source: number; period: string }
    kick: { streamers: string[] }
    youtube: { channels: string[] }
  }
  settings: { min_views: number; max_duration: number; shorts_threshold: number }
  channels: Record<string, { name: string; platform?: string; schedule: { shorts_per_day: number; release_times: string[] } }>
  upload: { default_privacy: string; max_uploads_per_day: number }
}
