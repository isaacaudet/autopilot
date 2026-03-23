<div align="center">

# AUTOPILOT

**Automated short-form content pipeline**

Twitch clips in. YouTube Shorts, Instagram Reels, TikTok, and Facebook Reels out.
<br>Zero manual input. Every day.

<br>

| 1M+ YouTube views | 700K Instagram views | 7 Shorts/day | 4 platforms |
|:---:|:---:|:---:|:---:|
| first month | first 3 days | fully automated | simultaneous cross-post |

<br>

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-61DAFB?style=flat-square&logo=react&logoColor=black)
![FFmpeg](https://img.shields.io/badge/FFmpeg-007808?style=flat-square&logo=ffmpeg&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?style=flat-square&logo=sqlite&logoColor=white)
![Whisper](https://img.shields.io/badge/Whisper-412991?style=flat-square&logo=openai&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini-8E75B2?style=flat-square&logo=google&logoColor=white)

</div>

---

## Pipeline

```
                    ┌─────────────────────────────────────────────────────────┐
                    │                                                         │
  Twitch Helix API  │   Score ─→ Screen ─→ Format ─→ Subtitle ─→ Burn       │
  ───────────────→  │     │                   │                    │          │
  clips by streamer │   velocity           16:9→9:16            overlays     │
  or game-wide      │   duration           facecam crop         hook text    │
                    │   audio quality      gameplay zoom        progress bar │
                    │   title keywords     HUD reposition       CTAs         │
                    │   learned weights    layout profiles      transitions  │
                    │                                                         │
                    └──────────────────────────┬──────────────────────────────┘
                                               │
                    ┌──────────────────────────┴──────────────────────────────┐
                    │                                                         │
                    │   Thumbnail ─→ Schedule ─→ Upload ─→ Cross-Post        │
                    │                                                         │
                    │   YouTube (publishAt)          Instagram (binary)       │
                    │   TikTok (direct post)         Facebook Reels           │
                    │                                                         │
                    │   ◆ Per-platform time-slot optimization                 │
                    │   ◆ Daily caps + posting gap enforcement                │
                    │   ◆ Platform-specific CTA overlays                      │
                    │                                                         │
                    └──────────────────────────┬──────────────────────────────┘
                                               │
                                               ▼
                                        Learn + Retrain
                                     (scoring weights adapt
                                      from upload performance)
```

## What each stage does

| Stage | What happens |
|:---|:---|
| **Fetch** | Pulls clips from Twitch Helix API — by configured streamer list or game-wide sweep. Deduplicates against SQLite history table. |
| **Score** | Ranks clips by view velocity, duration, title keywords, audio levels. Weights are trained from actual channel performance data. |
| **Screen** | Gemini evaluates content quality and generates optimized titles and descriptions per clip. |
| **Format** | Converts 16:9 source → 9:16 vertical. Facecam is auto-detected and cropped to fill the top band. Gameplay is zoomed and repositioned. Each streamer has a calibrated layout profile. |
| **Subtitle** | Whisper (medium) transcription with word-level timing. Outputs ASS format with Impact font, cyan karaoke highlight, and pop-in animation. |
| **Burn** | FFmpeg composites all layers: subtitles, hook text overlay, progress bar, subscribe/follow CTA animations. Compilations get transition cards with streamer branding. |
| **Thumbnail** | Per-clip branded thumbnails — full video frame, gradient overlay, game-specific color palette, streamer name + title text. |
| **Schedule** | Allocates upload time slots per platform. YouTube targets peak hours, Instagram is throttled to 2-3/day, cross-posts are staggered. |
| **Upload** | YouTube via Data API v3 with `publishAt` scheduling. Instagram and Facebook via binary upload. TikTok via direct post API. |
| **Learn** | Tracks views and engagement per upload. Periodically retrains scoring weights so clip selection improves over time. |

## Architecture

```
clipper/
├── workflow.py                  pipeline orchestration
├── api.py                       FastAPI + SSE streaming
├── db.py                        SQLite (WAL mode, auto-migration)
├── schedule.py                  time-slot allocation
├── crosspost.py                 cross-platform scheduling
├── learn.py                     performance tracking + weight training
│
├── fetch/
│   └── twitch.py                Twitch Helix API client
│
├── process/
│   ├── score.py                 multi-factor clip scoring
│   ├── analyze.py               Gemini content analysis
│   ├── format.py                16:9 → 9:16 (fill / blur modes)
│   ├── detect_facecam.py        facecam detection + layout calibration
│   ├── subtitles.py             Whisper → ASS, word-level timing
│   ├── burn.py                  FFmpeg compositing
│   ├── compile.py               daily compilation builder
│   ├── thumbnail.py             branded thumbnail generator
│   └── titles.py                Gemini title / description generation
│
└── upload/
    ├── youtube.py               YouTube Data API v3
    ├── instagram.py             Instagram Graph API (binary upload)
    ├── facebook.py              Facebook Reels (binary upload)
    ├── tiktok.py                TikTok direct post
    ├── auth.py                  OAuth flows (Google, Meta, TikTok)
    └── dispatcher.py            multi-channel upload routing

web/                             React + TypeScript + shadcn/ui
├── StudioPage.tsx               clip review + approval
├── EditPage.tsx                 layout + subtitle editor
└── AnalyticsPage.tsx            performance dashboard
```

## Daily output

```
07:00   Fetch fresh clips from Twitch
07:02   Score, screen, approve top candidates
07:05   Format → subtitle → burn (parallel processing)
07:30   Generate thumbnails
07:35   Upload 7 Shorts to YouTube (scheduled across peak hours)
07:40   Build + upload daily highlight compilation
09:00   Cross-post top clips to Instagram, TikTok, Facebook
17:00   Release executor publishes scheduled YouTube uploads
```

Runs unattended via launchd cron. The web dashboard is for reviewing clips and monitoring performance — not required for daily operation.

## Stack

| Component | Technology |
|:---|:---|
| Pipeline + API | Python 3.11+, FastAPI |
| Video processing | FFmpeg via `imageio-ffmpeg` (libass, loudnorm) |
| Speech-to-text | Whisper (medium), word-level alignment |
| Content analysis | Gemini (screening, titles, descriptions) |
| Database | SQLite with WAL mode |
| Frontend | React, TypeScript, shadcn/ui |
| Clip download | yt-dlp |
| Scheduling | macOS launchd |

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env            # Twitch, Meta, TikTok, Gemini API keys
python -m clipper auth           # OAuth setup for each platform
python -m clipper serve          # API + web UI → localhost:8420
```

All pipeline behavior is driven by `config.yaml` — streamer lists, scoring thresholds, daily caps, posting schedules, layout preferences. No secrets in config; credentials come from `.env` and OAuth token files.
