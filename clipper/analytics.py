"""YouTube Analytics dashboard for tracking clip performance."""

import html
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from rich.console import Console
from rich.table import Table

console = Console()
_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_YT_QUOTA_COOLDOWN_UNTIL = 0.0


def fetch_video_stats(video_id: str, verbose: bool = False) -> dict | None:
    """Fetch stats for a single video from YouTube Data API + Analytics API.

    Returns dict with views, likes, comments, watch_time_minutes,
    avg_view_duration_seconds, avg_view_percentage or None on failure.
    """
    try:
        from clipper.upload.auth import get_youtube_service, get_youtube_analytics_service
    except ImportError as e:
        console.print(f"[red]Import error: {e}[/red]")
        return None

    try:
        yt = get_youtube_service()
        response = yt.videos().list(part="statistics,contentDetails", id=video_id).execute()
        items = response.get("items", [])
        if not items:
            console.print(f"[yellow]Video {video_id} not found.[/yellow]")
            return None

        stats = items[0].get("statistics", {})
        content = items[0].get("contentDetails", {})

        result = {
            "video_id": video_id,
            "views": int(stats.get("viewCount", 0)),
            "likes": int(stats.get("likeCount", 0)),
            "comments": int(stats.get("commentCount", 0)),
            "duration": content.get("duration", ""),
        }

        try:
            yta = get_youtube_analytics_service()
            analytics = yta.reports().query(
                ids="channel==MINE",
                startDate="2020-01-01",
                endDate="2099-12-31",
                metrics="estimatedMinutesWatched,averageViewDuration,averageViewPercentage",
                filters=f"video=={video_id}",
            ).execute()

            rows = analytics.get("rows", [])
            if rows:
                row = rows[0]
                result["watch_time_minutes"] = row[0]
                result["avg_view_duration_seconds"] = row[1]
                result["avg_view_percentage"] = row[2]
        except Exception as e:
            if verbose:
                console.print(f"  [dim]Analytics API unavailable: {e}[/dim]")

        return result

    except Exception as e:
        console.print(f"[red]Failed to fetch stats for {video_id}: {e}[/red]")
        return None


def fetch_retention_curve(video_id: str, verbose: bool = False) -> list[float] | None:
    """Fetch audience retention curve for a video via YouTube Analytics API.

    Returns a list of watch ratios (0-100) at each elapsed time percentile,
    or None if unavailable. Costs 1 API quota unit.
    """
    try:
        from clipper.upload.auth import get_youtube_analytics_service
    except ImportError:
        return None

    try:
        yta = get_youtube_analytics_service()
        response = yta.reports().query(
            ids="channel==MINE",
            startDate="2020-01-01",
            endDate="2099-12-31",
            metrics="audienceWatchRatio",
            dimensions="elapsedVideoTimeRatio",
            filters=f"video=={video_id}",
        ).execute()

        rows = response.get("rows", [])
        if not rows:
            return None

        return [row[1] * 100 for row in rows]
    except Exception as e:
        if verbose:
            console.print(f"  [dim]Retention curve unavailable for {video_id}: {e}[/dim]")
        return None


def _safe_channel_key(channel: str | None) -> str:
    raw = str(channel or "default")
    key = re.sub(r"[^a-z0-9_-]+", "_", raw.lower()).strip("_")
    return key or "default"


def _channel_recent_cache_path(days: int, channel: str | None) -> Path:
    """Return a stable cache path for channel recent uploads."""
    try:
        from clipper.config import get_project_root
        root = get_project_root()
    except Exception:
        root = Path.cwd()
    ch_key = _safe_channel_key(channel)
    return root / "queue" / f"channel_recent_{ch_key}_{days}d.json"


def _load_channel_recent_cache(cache_path: Path) -> tuple[datetime | None, list[dict]]:
    try:
        payload = json.loads(cache_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None, []
    if not isinstance(payload, dict):
        return None, []
    videos = payload.get("videos")
    if not isinstance(videos, list):
        return None, []
    ts = payload.get("generated_at")
    if isinstance(ts, str):
        dt = _parse_iso_datetime(ts)
    else:
        dt = None
    return dt, videos


def _write_channel_recent_cache(cache_path: Path, videos: list[dict]) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "videos": videos,
                },
                indent=2,
            )
        )
    except OSError:
        # Cache is best-effort; failing to write shouldn't break the UI.
        return


def fetch_channel_recent_ex(
    *,
    config: dict | None = None,
    channel: str | None = None,
    days: int = 30,
    verbose: bool = False,
    refresh: bool = False,
    cache_ttl_seconds: int = 20 * 60,
) -> tuple[list[dict], str | None]:
    """Fetch recent uploads with stats for a specific configured channel.

    Returns list of dicts with video_id, title, published_at, views, likes, comments.

    Returns (videos, error_message). error_message is only set when the live fetch fails.
    """
    try:
        from clipper.config import load_config
        from clipper.upload.auth import get_youtube_service, get_youtube_service_for_channel
    except ImportError as e:
        if verbose:
            console.print(f"[red]Import error: {e}[/red]")
        return [], str(e)

    try:
        global _YT_QUOTA_COOLDOWN_UNTIL

        if config is None:
            config = load_config()

        cache_path = _channel_recent_cache_path(days, channel)
        cached_at, cached_videos = _load_channel_recent_cache(cache_path)
        now = datetime.now(timezone.utc)
        if cached_videos and cached_at and not refresh:
            age = (now - cached_at).total_seconds()
            if age <= float(cache_ttl_seconds):
                return cached_videos, None

        # Avoid hammering the API during a known quota outage.
        if (not refresh) and (time.time() < _YT_QUOTA_COOLDOWN_UNTIL) and cached_videos:
            if verbose:
                console.print("[yellow]YouTube quota recently exceeded; using cached analytics.[/yellow]")
            return cached_videos, "quotaExceeded"

        if channel:
            yt = get_youtube_service_for_channel(channel, config)
            channel_label = channel
        else:
            yt = get_youtube_service()
            channel_label = "default"

        channels = yt.channels().list(part="contentDetails", mine=True).execute()
        items = channels.get("items", [])
        if not items:
            msg = "No channel found."
            if verbose:
                console.print(f"[yellow]{msg}[/yellow]")
            return [], msg

        channel = items[0]
        uploads_playlist = (
            (channel.get("contentDetails") or {})
            .get("relatedPlaylists", {})
            .get("uploads")
        )
        if not uploads_playlist:
            msg = "No uploads playlist found."
            if verbose:
                console.print(f"[yellow]{msg}[/yellow]")
            return [], msg

        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        videos: list[dict] = []
        page_token = None
        reached_cutoff = False

        while True:
            response = yt.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=uploads_playlist,
                maxResults=50,
                pageToken=page_token,
            ).execute()

            for item in response.get("items", []):
                snippet = item.get("snippet") or {}
                published_at = str(snippet.get("publishedAt", "") or "")
                published_dt = _parse_iso_datetime(published_at)
                if published_dt and published_dt < cutoff:
                    reached_cutoff = True
                    break

                content = item.get("contentDetails") or {}
                video_id = content.get("videoId")
                if not video_id:
                    continue

                videos.append(
                    {
                        "channel": channel_label,
                        "video_id": str(video_id),
                        "title": html.unescape(str(snippet.get("title", "") or "")),
                        "published_at": published_at,
                    }
                )

            if reached_cutoff:
                break

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        if videos:
            video_ids = [v["video_id"] for v in videos]
            for i in range(0, len(video_ids), 50):
                batch_ids = video_ids[i:i + 50]
                stats_response = yt.videos().list(
                    part="statistics",
                    id=",".join(batch_ids),
                ).execute()

                stats_map = {}
                for item in stats_response.get("items", []):
                    s = item.get("statistics", {})
                    stats_map[item["id"]] = {
                        "views": int(s.get("viewCount", 0)),
                        "likes": int(s.get("likeCount", 0)),
                        "comments": int(s.get("commentCount", 0)),
                    }

                for v in videos:
                    if v["video_id"] in stats_map:
                        v.update(stats_map[v["video_id"]])

        _write_channel_recent_cache(cache_path, videos)
        return videos, None

    except Exception as e:
        # Try to detect quota errors; if so, cool down for a bit and serve stale cache if possible.
        try:
            from googleapiclient.errors import HttpError  # type: ignore
        except Exception:
            HttpError = None  # type: ignore

        reason = None
        message = str(e)
        if HttpError is not None and isinstance(e, HttpError):
            try:
                raw = getattr(e, "content", b"") or b""
                payload = json.loads(raw.decode("utf-8")) if raw else {}
                if isinstance(payload, dict):
                    eobj = payload.get("error") or {}
                    if isinstance(eobj, dict):
                        message = str(eobj.get("message") or message)
                        errors = eobj.get("errors") or []
                        if errors and isinstance(errors, list) and isinstance(errors[0], dict):
                            reason = errors[0].get("reason") or reason
            except Exception:
                pass

        if reason == "quotaExceeded" or "quotaExceeded" in message:
            _YT_QUOTA_COOLDOWN_UNTIL = time.time() + 30 * 60

        cache_path = _channel_recent_cache_path(days, channel)
        cached_at, cached_videos = _load_channel_recent_cache(cache_path)
        if cached_videos:
            if verbose:
                ts = cached_at.isoformat() if cached_at else "unknown time"
                console.print(f"[yellow]Using cached channel analytics from {ts} (fresh fetch failed).[/yellow]")
            return cached_videos, reason or message

        if verbose:
            console.print(f"[red]Failed to fetch channel videos: {e}[/red]")
        return [], reason or message


def fetch_channel_recent(
    days: int = 30,
    verbose: bool = False,
    refresh: bool = False,
    cache_ttl_seconds: int = 20 * 60,
    *,
    channel: str | None = None,
    config: dict | None = None,
) -> list[dict]:
    """Fetch recent uploads with stats from the channel (single-channel convenience wrapper)."""
    videos, _err = fetch_channel_recent_ex(
        config=config,
        channel=channel,
        days=days,
        verbose=verbose,
        refresh=refresh,
        cache_ttl_seconds=cache_ttl_seconds,
    )
    return videos


def fetch_channels_recent(
    config: dict,
    days: int = 30,
    verbose: bool = False,
    refresh: bool = False,
    cache_ttl_seconds: int = 20 * 60,
    channels: list[str] | None = None,
) -> tuple[list[dict], dict[str, str]]:
    """Fetch recent uploads across all configured channels and merge them."""
    channels_cfg = config.get("channels", {}) or {}
    if channels is None:
        channels = list(channels_cfg.keys()) if channels_cfg else ["default"]
        if "default" in channels_cfg:
            channels = ["default"] + [c for c in channels if c != "default"]

    merged: list[dict] = []
    errors: dict[str, str] = {}

    for ch in channels:
        vids, err = fetch_channel_recent_ex(
            config=config,
            channel=(ch if ch != "default" else "default"),
            days=days,
            verbose=verbose,
            refresh=refresh,
            cache_ttl_seconds=cache_ttl_seconds,
        )
        if err and not vids:
            errors[ch] = err
        elif err:
            # Cache served; still surface the warning.
            errors[ch] = err
        merged.extend(vids)

    def _published_key(v: dict) -> str:
        return str(v.get("published_at", ""))

    merged.sort(key=_published_key, reverse=True)
    return merged, errors

def _safe_median(values: list[int]) -> float:
    if not values:
        return 0.0
    try:
        return float(median(values))
    except Exception:
        return 0.0


def _parse_iso_hour(iso_dt: str) -> int | None:
    if not iso_dt:
        return None
    try:
        return datetime.fromisoformat(iso_dt.replace("Z", "+00:00")).hour
    except ValueError:
        return None


def _parse_iso_datetime(iso_dt: str) -> datetime | None:
    if not iso_dt:
        return None
    try:
        return datetime.fromisoformat(iso_dt.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalize_title(text: str) -> str:
    if not text:
        return ""
    out = html.unescape(text.lower())
    out = out.replace("#shorts", "")
    out = re.sub(r"[^a-z0-9]+", " ", out)
    out = " ".join(out.split())
    return out.strip()


def _load_output_meta_indexes(output_dir: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    """Build metadata lookup indexes by video_id and normalized title."""
    by_video: dict[str, dict] = {}
    by_title: dict[str, dict] = {}
    if not output_dir.exists():
        return by_video, by_title

    for meta_path in output_dir.glob("*.json"):
        try:
            data = json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        video_id = data.get("video_id")
        if not video_id or video_id == "previously_uploaded":
            video_id = None
        if video_id:
            by_video[str(video_id)] = data

        candidates = [
            str(data.get("title", "")),
            str(data.get("_title_override", "")),
            str(data.get("_generated_title", "")),
        ]
        for raw in candidates:
            norm = _normalize_title(raw)
            if norm:
                by_title.setdefault(norm, data)

    return by_video, by_title


def _aggregate_segment(rows: list[dict], key_name: str) -> list[dict]:
    """Aggregate uploads by a categorical key (game/streamer/category/hour slot)."""
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        key = str(row.get(key_name) or "Unknown")
        buckets.setdefault(key, []).append(row)

    out: list[dict] = []
    for key, items in buckets.items():
        views = [int(i.get("views", 0)) for i in items]
        qa_scores = [
            int(i.get("subtitle_qa_score"))
            for i in items
            if isinstance(i.get("subtitle_qa_score"), (int, float))
        ]
        uploads = len(items)
        total_views = sum(views)
        avg_views = (total_views / uploads) if uploads else 0.0
        med_views = _safe_median(views)
        winner_cutoff = max(100.0, med_views * 1.5)
        winners = sum(1 for v in views if v >= winner_cutoff)
        win_rate = winners / uploads if uploads else 0.0
        out.append(
            {
                "name": key,
                "uploads": uploads,
                "total_views": total_views,
                "avg_views": round(avg_views, 1),
                "median_views": round(med_views, 1),
                "winner_cutoff": round(winner_cutoff, 1),
                "win_rate": round(win_rate, 3),
                "avg_subtitle_qa": round(sum(qa_scores) / len(qa_scores), 1) if qa_scores else None,
            }
        )

    out.sort(
        key=lambda x: (
            x["name"] == "Unknown",
            -x["avg_views"],
            -x["win_rate"],
            -x["uploads"],
        )
    )
    return out


def build_posting_times(rows: list[dict], winner_cutoff: float, top_n: int = 8) -> list[dict]:
    """Return recommended publishing slots from historical view performance."""
    slot_views: dict[tuple[int, int], list[int]] = {}
    for row in rows:
        dt = _parse_iso_datetime(str(row.get("published_at", "")))
        if not dt:
            continue
        local = dt.astimezone()
        key = (local.weekday(), local.hour)
        slot_views.setdefault(key, []).append(int(row.get("views", 0)))

    scored: list[dict] = []
    for (weekday, hour), views in slot_views.items():
        uploads = len(views)
        if uploads < 2:
            continue
        avg_views = sum(views) / uploads
        winners = sum(1 for v in views if v >= winner_cutoff)
        win_rate = winners / uploads if uploads else 0.0
        score = avg_views * (0.7 + win_rate)
        scored.append(
            {
                "weekday": _WEEKDAYS[weekday] if 0 <= weekday < len(_WEEKDAYS) else str(weekday),
                "hour": f"{hour:02d}:00",
                "uploads": uploads,
                "avg_views": round(avg_views, 1),
                "win_rate": round(win_rate, 3),
                "score": round(score, 1),
            }
        )

    scored.sort(key=lambda x: (x["score"], x["avg_views"], x["uploads"]), reverse=True)
    return scored[:top_n]


def build_growth_scoreboard(config: dict, days: int = 90, refresh: bool = False, channel: str = "all") -> dict:
    """Build a growth-oriented analytics payload for the web app."""
    channel_key = str(channel or "all").strip()
    errors: dict[str, str] = {}
    if channel_key and channel_key.lower() not in ("all", "*"):
        videos, err = fetch_channel_recent_ex(
            config=config, channel=channel_key, days=days, verbose=False, refresh=refresh
        )
        if err:
            errors[channel_key] = err
    else:
        videos, errors = fetch_channels_recent(config, days=days, verbose=False, refresh=refresh)
    if not videos:
        notes = ["No analytics rows found in selected window."]
        if errors:
            # Keep this terse so it reads cleanly in the UI.
            joined = "; ".join(f"{k}: {v}" for k, v in list(errors.items())[:3])
            notes.append(f"YouTube analytics unavailable ({joined}).")
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "days": days,
            "channel": channel_key,
            "summary": {
                "uploads": 0,
                "total_views": 0,
                "avg_views": 0,
                "median_views": 0,
                "winner_cutoff": 0,
            },
            "top_videos": [],
            "by_game": [],
            "by_streamer": [],
            "by_category": [],
            "by_hour": [],
            "posting_times": [],
            "notes": notes,
        }

    output_dir = config["_output_dir"]
    local_meta_by_video, local_meta_by_title = _load_output_meta_indexes(output_dir)

    rows: list[dict] = []
    for v in videos:
        video_id = str(v.get("video_id", ""))
        meta = local_meta_by_video.get(video_id, {})
        if not meta:
            meta = local_meta_by_title.get(_normalize_title(str(v.get("title", ""))), {})
        analysis = meta.get("_analysis", {}) if isinstance(meta.get("_analysis"), dict) else {}
        qa = meta.get("_subtitle_qa", {}) if isinstance(meta.get("_subtitle_qa"), dict) else {}
        hour = _parse_iso_hour(str(v.get("published_at", "")))
        rows.append(
            {
                "video_id": video_id,
                "title": v.get("title", ""),
                "channel": v.get("channel", ""),
                "published_at": v.get("published_at", ""),
                "views": int(v.get("views", 0) or 0),
                "likes": int(v.get("likes", 0) or 0),
                "comments": int(v.get("comments", 0) or 0),
                "game": meta.get("game", "Unknown"),
                "streamer": meta.get("streamer", "Unknown"),
                "category": analysis.get("category", "unknown"),
                "subtitle_qa_score": qa.get("score"),
                "publish_hour": f"{hour:02d}:00" if hour is not None else "Unknown",
            }
        )

    rows.sort(key=lambda x: x["views"], reverse=True)
    views = [r["views"] for r in rows]
    uploads = len(rows)
    total_views = sum(views)
    avg_views = total_views / uploads if uploads else 0.0
    med_views = _safe_median(views)
    winner_cutoff = max(100.0, med_views * 1.5)

    by_game = _aggregate_segment(rows, "game")
    by_streamer = _aggregate_segment(rows, "streamer")
    by_category = _aggregate_segment(rows, "category")
    by_hour = _aggregate_segment(rows, "publish_hour")
    posting_times = build_posting_times(rows, winner_cutoff)

    notes: list[str] = []
    known_games = [g for g in by_game if str(g.get("name", "")).strip().lower() != "unknown"]
    known_streamers = [s for s in by_streamer if str(s.get("name", "")).strip().lower() != "unknown"]
    if known_games:
        best_game = known_games[0]
        notes.append(
            f"Best game in last {days}d: {best_game['name']} "
            f"({best_game['avg_views']:.0f} avg views across {best_game['uploads']} uploads)."
        )
    if known_streamers:
        best_streamer = known_streamers[0]
        notes.append(
            f"Best streamer segment: {best_streamer['name']} "
            f"({best_streamer['avg_views']:.0f} avg views)."
        )
    low_win_games = [g for g in known_games if g["uploads"] >= 3 and g["win_rate"] <= 0.1]
    if low_win_games:
        sample = ", ".join(g["name"] for g in low_win_games[:3])
        notes.append(f"Low-conversion segments to deprioritize: {sample}.")
    if errors:
        joined = "; ".join(f"{k}: {v}" for k, v in list(errors.items())[:3])
        notes.append(f"YouTube analytics warning ({joined}).")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "channel": channel_key,
        "summary": {
            "uploads": uploads,
            "total_views": total_views,
            "avg_views": round(avg_views, 1),
            "median_views": round(med_views, 1),
            "winner_cutoff": round(winner_cutoff, 1),
        },
        "top_videos": rows[:20],
        "by_game": by_game[:20],
        "by_streamer": by_streamer[:20],
        "by_category": by_category[:20],
        "by_hour": by_hour[:24],
        "posting_times": posting_times,
        "notes": notes,
    }


def build_kill_scale_recommendations(
    config: dict,
    hours: int = 2,
    baseline_days: int = 90,
    refresh: bool = False,
    board: dict | None = None,
    channel: str = "all",
) -> dict:
    """Classify newly published videos into hold/kill/scale actions."""
    if board is None:
        board = build_growth_scoreboard(config, days=baseline_days, refresh=refresh, channel=channel)
    summary = board.get("summary", {})
    median_views = float(summary.get("median_views", 0))
    winner_cutoff = float(summary.get("winner_cutoff", 100))
    kill_cutoff = max(50.0, median_views * 0.5)

    channel_key = str(channel or "all").strip()
    if channel_key and channel_key.lower() not in ("all", "*"):
        recent, _err = fetch_channel_recent_ex(
            config=config, channel=channel_key, days=3, verbose=False, refresh=refresh
        )
    else:
        recent, _errs = fetch_channels_recent(config, days=3, verbose=False, refresh=refresh)
    now = datetime.now(timezone.utc)

    actions: list[dict] = []
    for row in recent:
        dt = _parse_iso_datetime(str(row.get("published_at", "")))
        if not dt:
            continue
        age_hours = (now - dt).total_seconds() / 3600
        if age_hours < 0 or age_hours > hours:
            continue
        views = int(row.get("views", 0))
        if views >= winner_cutoff:
            action = "scale"
            reason = f"{views:,} views exceeds winner cutoff {winner_cutoff:.0f}."
        elif views <= kill_cutoff:
            action = "kill"
            reason = f"{views:,} views below kill threshold {kill_cutoff:.0f}."
        else:
            action = "hold"
            reason = "Performance is inside expected range."

        actions.append(
            {
                "video_id": row.get("video_id"),
                "title": row.get("title", ""),
                "published_at": row.get("published_at", ""),
                "age_hours": round(age_hours, 2),
                "views": views,
                "action": action,
                "reason": reason,
            }
        )

    actions.sort(key=lambda x: x["published_at"], reverse=True)
    return {
        "hours_window": hours,
        "kill_threshold": round(kill_cutoff, 1),
        "scale_threshold": round(winner_cutoff, 1),
        "actions": actions,
    }


def display_analytics_dashboard(videos: list[dict]) -> None:
    """Display a rich table of video analytics sorted by views."""
    if not videos:
        console.print("[yellow]No videos to display.[/yellow]")
        return

    videos.sort(key=lambda v: v.get("views", 0), reverse=True)

    table = Table(title="YouTube Analytics Dashboard")
    table.add_column("#", style="dim", width=3)
    table.add_column("Title", max_width=45)
    table.add_column("Views", justify="right", style="cyan")
    table.add_column("Likes", justify="right", style="green")
    table.add_column("Comments", justify="right")
    table.add_column("Published", style="dim")

    for i, v in enumerate(videos, 1):
        views = v.get("views", 0)
        if views >= 10000:
            view_style = "[bold green]"
        elif views >= 1000:
            view_style = "[green]"
        elif views >= 100:
            view_style = "[yellow]"
        else:
            view_style = "[dim]"

        published = v.get("published_at", "")[:10]
        title = v.get("title", "?")
        if len(title) > 45:
            title = title[:42] + "..."

        table.add_row(
            str(i),
            title,
            f"{view_style}{views:,}[/]",
            str(v.get("likes", 0)),
            str(v.get("comments", 0)),
            published,
        )

    console.print(table)

    total_views = sum(v.get("views", 0) for v in videos)
    total_likes = sum(v.get("likes", 0) for v in videos)
    avg_views = total_views / len(videos) if videos else 0
    console.print(
        f"\n[bold]Total:[/bold] {total_views:,} views, {total_likes:,} likes "
        f"across {len(videos)} videos (avg {avg_views:,.0f} views/video)"
    )
