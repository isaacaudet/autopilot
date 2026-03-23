"""Release queue management — schedule clips to channels and publish on time."""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)

console = Console()



def _load_channel_config(channel: str, config: dict) -> dict:
    """Load a channel's config from the channels section. Raises if not found."""
    channels = config.get("channels", {})
    if channel not in channels:
        raise ValueError(
            f"Channel '{channel}' not found in config.yaml. "
            f"Available: {', '.join(channels.keys()) or '(none)'}"
        )
    return channels[channel]


def get_next_slots(channel: str, config: dict, count: int = 1, *, today_only: bool = False) -> list[datetime]:
    """Return the next N available release times for a channel (naive PST datetimes).

    Reads the channel's schedule (shorts_per_day, release_times) and scans
    existing releases to find unfilled slots starting from now.
    All times use fixed PST (UTC-8) — no daylight saving shifts.
    Callers must convert to UTC before storing.

    today_only=True: only return slots within the current calendar day (PST).
    Prevents Shorts from bleeding into tomorrow when today's slots are full.
    """
    from zoneinfo import ZoneInfo
    from datetime import timezone
    from clipper.db import list_releases

    _pst = timezone(timedelta(hours=-8))  # Fixed PST, no DST
    _utc = ZoneInfo("UTC")

    ch_config = _load_channel_config(channel, config)
    schedule = ch_config.get("schedule", {})
    release_times = schedule.get("release_times", ["12:00"])

    # Parse release times into (hour, minute) tuples
    time_slots = []
    for t in release_times:
        try:
            parts = t.split(":")
            time_slots.append((int(parts[0]), int(parts[1])))
        except (IndexError, ValueError):
            logger.warning("Invalid release_time format %r — skipping", t)
    if not time_slots:
        time_slots = [(12, 0)]
    time_slots.sort()

    # Load existing releases — build "taken" as naive PST ISO strings for comparison
    releases = list_releases(config, channel=channel)
    taken: set[str] = set()
    for r in releases:
        if r.get("status") == "failed":
            continue
        sched = r.get("scheduled_at", "")
        if not sched:
            continue
        try:
            # Stored as UTC (with Z or +00:00) → convert to naive PST for slot matching
            dt_utc = datetime.fromisoformat(sched.replace("Z", "+00:00"))
            dt_pst = dt_utc.astimezone(_pst).replace(tzinfo=None)
            taken.add(dt_pst.strftime("%Y-%m-%dT%H:%M:%S"))
        except (ValueError, TypeError):
            taken.add(sched)  # legacy naive strings — match as-is

    # Find next available slots (in fixed PST)
    now_pt = datetime.now(tz=_pst).replace(tzinfo=None)
    slots = []
    day = now_pt.date()
    today = now_pt.date()

    # Look up to 30 days ahead (today_only restricts to current calendar day)
    for _ in range(30):
        if today_only and day > today:
            break
        for hour, minute in time_slots:
            candidate = datetime(day.year, day.month, day.day, hour, minute)
            if candidate <= now_pt:
                continue
            iso = candidate.strftime("%Y-%m-%dT%H:%M:%S")
            if iso not in taken:
                slots.append(candidate)
                taken.add(iso)  # Don't double-assign
                if len(slots) >= count:
                    return slots
        day += timedelta(days=1)

    return slots


def schedule_release(
    clip_id: str,
    channel: str,
    scheduled_at: datetime,
    config: dict,
    meta_path: str | None = None,
    privacy: str | None = None,
) -> int:
    """Create a release queue entry. Returns release ID.

    scheduled_at is a naive PST datetime from get_next_slots. Stored as UTC ISO
    so SQLite datetime('now') comparisons work correctly in any environment.
    """
    from datetime import timezone as _tz
    from clipper.db import create_release

    ch_config = _load_channel_config(channel, config)

    if privacy is None:
        privacy = ch_config.get("default_privacy", "unlisted")

    # Convert naive PST (fixed UTC-8) → UTC for consistent DB storage
    _pst = _tz(timedelta(hours=-8))
    scheduled_utc = scheduled_at.replace(tzinfo=_pst).astimezone(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return create_release(
        config, clip_id, channel, scheduled_utc,
        privacy=privacy, meta_path=meta_path,
    )


def get_pending_releases(config: dict) -> list[dict]:
    """Load all releases sorted by scheduled_at."""
    from clipper.db import list_releases
    return list_releases(config)


_MAX_RETRIES = 3
_MAX_SUBSTITUTIONS = 3  # Don't chain-substitute forever


def _substitute_failed_release(config: dict, failed_release: dict) -> bool:
    """When a release fails permanently, find another clip to take its slot.

    Picks the highest-scoring processed clip that hasn't been uploaded to the
    same platform yet. Returns True if a substitute was queued.
    """
    from clipper.db import get_db, create_release

    channel = failed_release.get("channel", "")
    if not channel:
        return False

    conn = get_db(config)
    from clipper.upload.dispatcher import get_channel_platform, platform_id_column
    platform = get_channel_platform(channel, config)
    id_col = platform_id_column(platform)

    # Use the game bound to this channel from config — prevents cross-game contamination
    channel_config = config.get("channels", {}).get(channel, {})
    channel_game = channel_config.get("game", "")

    if not channel_game:
        console.print(f"[red]Channel {channel} has no game configured — refusing to substitute[/red]")
        return False

    rows = conn.execute(f"""
        SELECT c.id, c.title, c.streamer, c.processed_path
        FROM clips c
        WHERE c.processed_path IS NOT NULL
          AND c.status IN ('approved', 'output')
          AND (c.{id_col} IS NULL OR c.{id_col} = '')
          AND c.game = ?
          AND (c.processed_path LIKE '%_final%' OR c.processed_path LIKE '%_shorts%'
               OR c.processed_path LIKE '%_clean%' OR c.processed_path LIKE '%compilation%')
          AND c.id NOT IN (
              SELECT clip_id FROM releases
              WHERE channel = ? AND status IN ('pending', 'uploaded', 'executing', 'published')
          )
        ORDER BY c.score DESC
        LIMIT 20
    """, (channel_game, channel)).fetchall()

    sub_clip = None
    for row in rows:
        candidate = dict(row)
        ppath = candidate.get("processed_path") or ""
        if ppath and not Path(ppath).exists():
            # Missing file — mark skipped so it won't be picked again
            from clipper.db import update_clip
            update_clip(config, candidate["id"], status="skipped")
            logger.debug("Substitute skipped (missing file): %s", ppath)
            continue
        sub_clip = candidate
        break

    if not sub_clip:
        logger.debug("No substitute clip available for channel %s", channel)
        return False

    # Queue immediately (scheduled_at = now)
    from datetime import datetime
    now_utc = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    privacy = failed_release.get("privacy", "public")

    create_release(
        config, sub_clip["id"], channel, now_utc,
        privacy=privacy, platform=platform, status="pending",
    )
    console.print(
        f"[cyan]  Substituted:[/cyan] {sub_clip['streamer']} — {sub_clip['title'][:40]} → {channel}"
    )
    return True

# HTTP status codes that indicate transient (retryable) failures
_TRANSIENT_STATUS_CODES = {500, 502, 503, 504, 408, 429}


def _is_transient_error(exc: Exception) -> bool:
    """Check if an upload exception is transient (worth retrying)."""
    err_str = str(exc).lower()
    # Permanent failures — don't retry
    if any(kw in err_str for kw in ("401", "403", "invalid", "expired", "unauthorized")):
        return False
    # Transient patterns
    if any(kw in err_str for kw in ("timeout", "500", "502", "503", "504", "429", "connection")):
        return True
    # requests.HTTPError with status code
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if status and status in _TRANSIENT_STATUS_CODES:
        return True
    return False


def execute_releases(config: dict, verbose: bool = False) -> int:
    """Upload + publish all releases whose time has come. Returns count published."""
    from clipper.upload.dispatcher import upload_clip, publish_video, get_channel_platform, platform_id_column
    from clipper.db import pending_releases_due, update_release, update_clip as db_update_clip

    due = pending_releases_due(config)
    published = 0

    for data in due:
        status = data.get("status", "")
        release_id = data.get("id")
        retry_count = int(data.get("retry_count") or 0)

        # Recover stale 'executing' releases (process crashed mid-upload)
        if status == "executing":
            if retry_count >= _MAX_RETRIES:
                update_release(config, release_id, status="failed",
                               last_error="max retries exhausted (recovered from stale executing)")
                console.print(f"[red]Release {release_id}: marked failed (stale, retries exhausted)[/red]")
                _substitute_failed_release(config, data)
            else:
                update_release(config, release_id, status="pending")
                status = "pending"  # fall through to process it

        # Upload pending releases
        if status == "pending":
            # Atomic claim: only proceed if we can set status to 'executing'.
            # Prevents two concurrent cron runs from uploading the same release.
            from clipper.db import get_db
            conn = get_db(config)
            claimed = conn.execute(
                "UPDATE releases SET status = 'executing' WHERE id = ? AND status = 'pending'",
                (release_id,),
            )
            conn.commit()
            if claimed.rowcount == 0:
                continue  # another process already claimed it

            # Check if this clip already has the platform ID set (crosspost.py may have uploaded it)
            platform = get_channel_platform(data.get("channel", ""), config)
            id_col = platform_id_column(platform)
            clip_id_check = data.get("clip_id", "")
            if clip_id_check:
                from clipper.db import get_clip
                existing = get_clip(config, clip_id_check)
                if existing and existing.get(id_col):
                    update_release(config, release_id, status="published",
                                   video_id=existing[id_col])
                    console.print(f"[dim]Skipping {clip_id_check[:20]} — already on {platform}, substituting[/dim]")
                    _substitute_failed_release(config, data)
                    published += 1
                    continue
            clip = None
            meta_path = data.get("meta_path")
            if meta_path and Path(meta_path).exists():
                try:
                    with open(meta_path) as f:
                        clip = json.load(f)
                except (OSError, json.JSONDecodeError) as e:
                    console.print(f"[yellow]Meta file unreadable for {data.get('clip_id')}: {e}[/yellow]")

            # Fall back to loading clip from DB when meta file is missing/corrupt
            if not clip:
                clip_id = data.get("clip_id", "")
                if clip_id:
                    from clipper.db import get_clip
                    clip = get_clip(config, clip_id)
                if not clip:
                    console.print(f"[yellow]No meta file or DB record for {data.get('clip_id')}, skipping.[/yellow]")
                    update_release(config, release_id, status="failed", last_error="missing meta file and DB record")
                    _substitute_failed_release(config, data)
                    continue
                console.print(f"[dim]Loaded clip from DB (meta file missing)[/dim]")

            # Guard: skip releases whose processed file no longer exists on disk
            ppath = clip.get("processed_path") or ""
            if ppath and not Path(ppath).exists():
                update_release(config, release_id, status="cancelled",
                               last_error="processed file missing")
                clip_id_str = data.get("clip_id", "")
                if clip_id_str:
                    db_update_clip(config, clip_id_str, status="skipped")
                console.print(f"[yellow]  Skipped: processed file missing — {Path(ppath).name}[/yellow]")
                continue

            channel = data.get("channel", "")
            privacy = data.get("privacy", "unlisted")
            platform = get_channel_platform(channel, config)

            console.print(f"[bold]Uploading:[/bold] {clip.get('title', '?')[:50]} → {channel} ({platform})")

            video_id = None
            upload_error = None
            try:
                video_id = upload_clip(clip, config, privacy=privacy, verbose=verbose, channel=channel)
            except Exception as e:
                upload_error = e

            if video_id:
                update_release(config, release_id, status="uploaded", video_id=video_id)

                # Store in correct field
                id_col = platform_id_column(platform)
                clip[id_col] = video_id
                if meta_path:
                    with open(meta_path, "w") as f:
                        json.dump(clip, f, indent=2)

                # Update DB
                clip_id = data.get("clip_id", "")
                if clip_id:
                    db_update_clip(config, clip_id, **{id_col: video_id})

                console.print(f"[green]  Uploaded ({platform}):[/green] {video_id}")
            else:
                error_msg = str(upload_error) if upload_error else "upload returned None"
                # Retry transient failures up to _MAX_RETRIES
                if retry_count < _MAX_RETRIES and (upload_error is None or _is_transient_error(upload_error)):
                    new_count = retry_count + 1
                    update_release(config, release_id, status="pending",
                                   retry_count=new_count, last_error=error_msg[:500])
                    console.print(f"[yellow]  Upload failed (retry {new_count}/{_MAX_RETRIES}): {error_msg[:100]}[/yellow]")
                else:
                    update_release(config, release_id, status="failed", last_error=error_msg[:500])
                    console.print(f"[red]  Upload failed permanently: {error_msg[:100]}[/red]")
                    _substitute_failed_release(config, data)
            continue

        # Publish uploaded releases
        if status == "uploaded":
            video_id = data.get("video_id")
            if not video_id:
                logger.warning("Release %s has status=uploaded but no video_id — marking failed", release_id)
                update_release(config, release_id, status="failed", last_error="no video_id")
                _substitute_failed_release(config, data)
                continue
            channel = data.get("channel", "")
            try:
                if publish_video(video_id, verbose=verbose, channel=channel, config=config):
                    update_release(config, release_id, status="published")
                    published += 1
            except Exception as e:
                error_msg = str(e)
                if retry_count < _MAX_RETRIES and _is_transient_error(e):
                    new_count = retry_count + 1
                    update_release(config, release_id, status="uploaded",
                                   retry_count=new_count, last_error=error_msg[:500])
                    console.print(f"[yellow]  Publish failed (retry {new_count}/{_MAX_RETRIES}): {error_msg[:100]}[/yellow]")
                else:
                    update_release(config, release_id, status="failed", last_error=error_msg[:500])
                    console.print(f"[red]  Publish failed permanently: {error_msg[:100]}[/red]")
                    _substitute_failed_release(config, data)

    if published:
        console.print(f"\n[bold green]{published} video(s) published.[/bold green]")
    elif verbose:
        console.print("[dim]No releases due right now.[/dim]")

    # Weekly purge of old output files (Sundays only, keeps last 7 days)
    if datetime.now().weekday() == 6:  # Sunday
        try:
            purge_old_output(config)
        except Exception as e:
            logger.warning("Output purge failed: %s", e)

    return published


def purge_old_output(config: dict, keep_days: int = 7) -> dict:
    """Delete processed output files older than keep_days, preserving files needed by pending releases."""
    from clipper.db import get_db

    out_dir = config.get("_output_dir") or Path("output")
    out_dir = Path(out_dir)
    conn = get_db(config)

    needed: set[str] = set()
    for row in conn.execute(
        "SELECT meta_path FROM releases WHERE status IN ('pending','uploaded','scheduled') AND meta_path IS NOT NULL"
    ).fetchall():
        if row["meta_path"]:
            needed.add(row["meta_path"])
    for row in conn.execute(
        "SELECT c.processed_path, c.source_path, c.subtitle_path FROM releases rel JOIN clips c ON rel.clip_id = c.id "
        "WHERE rel.status IN ('pending','uploaded','scheduled') AND c.processed_path IS NOT NULL"
    ).fetchall():
        if row["processed_path"]:
            needed.add(row["processed_path"])
        if row["source_path"]:
            needed.add(row["source_path"])
        if row["subtitle_path"]:
            needed.add(row["subtitle_path"])

    cutoff = datetime.now() - timedelta(days=keep_days)
    deleted = 0
    freed = 0
    purgeable_exts = {".mp4", ".ass", ".srt", ".jpg", ".json"}

    for f in out_dir.iterdir():
        if str(f) in needed or f.suffix.lower() not in purgeable_exts:
            continue
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        size = f.stat().st_size
        # Never purge compilation files — they are long-form deliverables, not transient output
        if f.stem.startswith("compilation_"):
            continue
        # Protect any mp4 created/modified within last 4 hours (in-progress jobs)
        if f.suffix.lower() == ".mp4" and (datetime.now() - mtime).total_seconds() < 14400:
            continue
        # Keep recent finals and their sidecars
        if f.name.endswith("_final.mp4") and mtime >= cutoff:
            continue
        if f.suffix in (".ass", ".json", ".jpg", ".srt"):
            stem = f.stem
            final = out_dir / (stem + "_final.mp4") if not stem.endswith("_final") else out_dir / (stem + ".mp4")
            if final.exists() and datetime.fromtimestamp(final.stat().st_mtime) >= cutoff:
                continue
        try:
            f.unlink()
            deleted += 1
            freed += size
        except Exception:
            pass

    freed_gb = freed / (1024 ** 3)
    if deleted:
        console.print(f"[dim]Purged {deleted} old output files ({freed_gb:.1f} GB freed)[/dim]")
    return {"deleted": deleted, "freed_gb": round(freed_gb, 2)}


def show_calendar(config: dict):
    """Display the upcoming release schedule."""
    releases = get_pending_releases(config)

    if not releases:
        console.print("[yellow]No scheduled releases.[/yellow]")
        return

    # Group by channel
    by_channel: dict[str, list[dict]] = {}
    for r in releases:
        ch = r.get("channel", "unknown")
        by_channel.setdefault(ch, []).append(r)

    channels = config.get("channels", {})

    for channel, items in by_channel.items():
        ch_config = channels.get(channel, {})
        display_name = ch_config.get("name", channel)

        table = Table(title=f"{channel} — {display_name}")
        table.add_column("Date", style="cyan")
        table.add_column("Time", style="cyan")
        table.add_column("Status")
        table.add_column("Clip")

        for item in items:
            scheduled = item.get("scheduled_at", "")
            try:
                dt = datetime.fromisoformat(scheduled)
                date_str = dt.strftime("%b %d")
                time_str = dt.strftime("%H:%M")
            except (ValueError, TypeError):
                date_str = "?"
                time_str = "?"

            status = item.get("status", "?")
            status_display = {
                "pending": "[dim]○ pending[/dim]",
                "uploaded": "[yellow]↑ uploaded[/yellow]",
                "published": "[green]✓ published[/green]",
                "failed": "[red]✗ failed[/red]",
            }.get(status, status)

            # Try to load clip title from meta
            clip_label = item.get("clip_id", "?")[:12]
            meta_path = item.get("meta_path")
            if meta_path and Path(meta_path).exists():
                try:
                    with open(meta_path) as f:
                        meta = json.load(f)
                    streamer = meta.get("streamer", "")
                    title = meta.get("title", "")[:30]
                    clip_label = f"{streamer} — {title}" if streamer else title
                except (json.JSONDecodeError, OSError):
                    pass

            table.add_row(date_str, time_str, status_display, clip_label)

        console.print(table)
        console.print()
