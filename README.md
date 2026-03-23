# Clipper

Automated Twitch-to-YouTube/Instagram/TikTok/Facebook clip pipeline. Fetches top clips, scores them, formats for Shorts/Reels, adds subtitles and overlays, uploads on a schedule, and cross-posts across platforms.

**1M+ YouTube views in the first month. 700K Instagram views in 3 days.**

## What it does

1. **Fetch** — Pulls clips from Twitch (by streamer or game-wide) via Helix API, deduplicates against history
2. **Score** — Ranks clips by view velocity, duration, title keywords, audio quality, and learned weights from channel performance
3. **Analyze** — Gemini pre-screens candidates for content quality and generates optimized titles/descriptions
4. **Format** — Converts 16:9 clips to 9:16 Shorts with facecam crop, gameplay zoom, HUD overlay, and streamer-specific layout profiles
5. **Subtitles** — Whisper transcription with word-level timing, ASS format, Impact font, cyan karaoke highlight, pop animation
6. **Burn** — Composites subtitles, hook text, progress bar, subscribe/follow CTA animations, and transition cards (compilations)
7. **Thumbnail** — Per-clip branded thumbnails with gradient overlay, streamer name, and game-specific color schemes
8. **Upload** — YouTube (scheduled with publishAt), Instagram (binary upload), TikTok, Facebook Reels
9. **Schedule** — Time-slot optimization per platform (YouTube peak hours, Instagram throttling, cross-post gaps)
10. **Learn** — Tracks upload performance, trains scoring weights, adjusts clip selection over time

## Architecture

```
clipper/
├── api.py              # FastAPI backend with SSE streaming
├── cli.py              # CLI: serve, release, auth
├── config.py           # Config loader + FFmpeg path resolution
├── cron.py             # Launchd cron job management
├── crosspost.py        # Cross-platform scheduling
├── db.py               # SQLite (WAL mode, auto-migration)
├── learn.py            # Performance tracking + weight training
├── schedule.py         # Time-slot allocation
├── workflow.py         # Central pipeline orchestration
├── fetch/
│   └── twitch.py       # Twitch Helix API client
├── process/
│   ├── analyze.py      # Gemini content analysis
│   ├── burn.py         # FFmpeg compositing (subtitles, overlays, CTAs)
│   ├── compile.py      # Daily compilation builder
│   ├── detect_facecam.py  # Facecam detection + layout calibration
│   ├── format.py       # 16:9 → 9:16 conversion (fill/blur modes)
│   ├── score.py        # Multi-factor clip scoring
│   ├── subtitles.py    # Whisper transcription + ASS generation
│   ├── thumbnail.py    # Branded thumbnail generator
│   └── titles.py       # Gemini title/description generation
└── upload/
    ├── auth.py         # OAuth flows (Google, Meta, TikTok)
    ├── dispatcher.py   # Multi-channel upload routing
    ├── facebook.py     # Facebook Reels (binary upload)
    ├── instagram.py    # Instagram Reels (binary upload)
    ├── tiktok.py       # TikTok direct post
    └── youtube.py      # YouTube Data API v3

web/                    # React + TypeScript + shadcn/ui dashboard
├── src/
│   ├── pages/
│   │   ├── StudioPage.tsx     # Clip review + approval
│   │   ├── EditPage.tsx       # Layout/subtitle editor
│   │   └── AnalyticsPage.tsx  # Performance dashboard
│   └── lib/
│       └── api.ts      # API client
config.yaml             # Streamer list, scoring params, schedule config
```

## Stack

- **Python 3.11+** — pipeline, API, all processing
- **FFmpeg** (via `imageio-ffmpeg`) — video processing with full libass support
- **Whisper** (medium) — speech-to-text with word-level timing
- **Gemini** — content analysis, title generation, pre-screening
- **SQLite** (WAL mode) — clips, releases, history, scoring weights, facecam profiles
- **FastAPI** — backend API with SSE for real-time pipeline state
- **React + TypeScript + shadcn/ui** — web dashboard for clip review, editing, analytics
- **yt-dlp** — Twitch clip downloading
- **Launchd** — macOS cron scheduling (autopilot, compilations, releases, cross-posts)

## Channels

Currently running two YouTube channels (Deadlock + class_instantiate) with cross-posting to Instagram (2 accounts), TikTok, and Facebook Reels.

Daily autopilot:
- **7 Shorts/day** per YouTube channel, scheduled across peak hours
- **Daily highlight compilations** (Deadlock + Marathon)
- **Instagram**: 2-3 high-quality clips/day per account (quality-gated)
- **Cross-posting** with platform-specific CTA overlays (YouTube subscribe animation, Instagram follow pill)

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env  # Add API keys: TWITCH_CLIENT_ID/SECRET, GOOGLE_API_KEY, etc.
python -m clipper auth  # OAuth setup for YouTube, Instagram, TikTok, Facebook
python -m clipper serve  # Start API + web UI
```

## License

Private.
