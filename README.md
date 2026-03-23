# Autopilot

Fully automated clip pipeline that discovers top Twitch clips, scores and ranks them, formats for vertical video (Shorts/Reels), adds subtitles and overlays, and publishes across YouTube, Instagram, TikTok, and Facebook on a daily schedule — zero manual input.

**1M+ YouTube views in the first month. 700K Instagram views in 3 days.**

## How it works

```
Twitch Clips → Score → Gemini Screen → Format 9:16 → Subtitles → Burn Overlays → Upload → Schedule → Cross-Post
```

1. **Fetch** — Pulls clips from Twitch via Helix API (by streamer list or game-wide sweep), deduplicates against history
2. **Score** — Ranks by view velocity, duration, title quality, audio levels; weights trained from past channel performance
3. **Screen** — Gemini pre-screens for content quality, generates optimized titles and descriptions
4. **Format** — Converts 16:9 → 9:16 with facecam crop, gameplay zoom, HUD repositioning — each streamer gets a calibrated layout profile
5. **Subtitle** — Whisper transcription → word-level ASS subtitles with karaoke highlight and pop animation
6. **Burn** — Composites everything: subtitles, hook text, progress bar, subscribe/follow CTAs, transition cards for compilations
7. **Thumbnail** — Branded per-clip thumbnails with game-specific color schemes
8. **Upload** — YouTube (scheduled publishAt), Instagram/Facebook (binary upload), TikTok (direct post)
9. **Schedule** — Per-platform time-slot optimization with configurable daily caps and posting gaps
10. **Learn** — Tracks views/engagement per upload, retrains scoring weights, improves selection over time

## Architecture

```
clipper/
├── workflow.py              # Pipeline orchestration
├── api.py                   # FastAPI + SSE streaming
├── db.py                    # SQLite (WAL mode, auto-migration)
├── schedule.py              # Time-slot allocation
├── crosspost.py             # Cross-platform scheduling
├── learn.py                 # Performance tracking + weight training
├── fetch/
│   └── twitch.py            # Twitch Helix API client
├── process/
│   ├── score.py             # Multi-factor clip scoring
│   ├── analyze.py           # Gemini content analysis
│   ├── format.py            # 16:9 → 9:16 (fill/blur modes)
│   ├── detect_facecam.py    # Facecam detection + layout calibration
│   ├── subtitles.py         # Whisper → ASS with word-level timing
│   ├── burn.py              # FFmpeg compositing
│   ├── compile.py           # Daily compilation builder
│   ├── thumbnail.py         # Branded thumbnail generator
│   └── titles.py            # Gemini title/description generation
└── upload/
    ├── youtube.py           # YouTube Data API v3
    ├── instagram.py         # Instagram Graph API (binary upload)
    ├── facebook.py          # Facebook Reels (binary upload)
    ├── tiktok.py            # TikTok direct post
    ├── auth.py              # OAuth flows (Google, Meta, TikTok)
    └── dispatcher.py        # Multi-channel upload routing

web/                         # React + TypeScript + shadcn/ui
├── StudioPage.tsx           # Clip review + approval
├── EditPage.tsx             # Layout + subtitle editor
└── AnalyticsPage.tsx        # Performance dashboard
```

## Stack

- **Python 3.11+** — pipeline, API, processing
- **FFmpeg** via `imageio-ffmpeg` — video compositing with libass
- **Whisper** (medium) — speech-to-text
- **Gemini** — content analysis and title generation
- **SQLite** — clips, releases, history, scoring weights, layout profiles
- **FastAPI** — backend with SSE for real-time pipeline state
- **React + shadcn/ui** — web dashboard
- **yt-dlp** — clip downloading

## Daily output

- **7 Shorts/day** per YouTube channel, scheduled across peak hours
- **Daily highlight compilations** with transition cards and mixed audio
- **2-3 Reels/day** per Instagram account (quality-gated, not all clips)
- **Cross-posting** to TikTok and Facebook with platform-specific CTA overlays
- Runs unattended via launchd cron — fetch at 7am, compile, upload, cross-post throughout the day

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env  # Add API keys (Twitch, Meta, TikTok, Gemini)
python -m clipper auth  # OAuth setup for each platform
python -m clipper serve  # Start API + web UI on localhost:8420
```

## Configuration

All pipeline behavior is driven by `config.yaml` — streamer lists, scoring thresholds, daily caps, posting schedules, layout preferences. No secrets in config; credentials come from `.env` and OAuth token files.

## License

Private.
