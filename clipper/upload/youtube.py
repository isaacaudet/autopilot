"""YouTube upload with optimized metadata for discoverability."""

import re
import subprocess
from pathlib import Path

from googleapiclient.http import MediaFileUpload
from rich.console import Console

from clipper.upload.auth import get_youtube_service

console = Console()

# Common game name → tag expansions for search volume
GAME_TAG_EXPANSIONS = {
    "Deadlock": ["deadlock", "deadlock game", "valve deadlock", "deadlock clips", "deadlock gameplay"],
    "ARC Raiders": ["arc raiders", "arc raiders clips", "arc raiders gameplay"],
    "League of Legends": ["league of legends", "lol", "league", "riot games"],
    "Valorant": ["valorant", "valo", "valorant clips"],
    "Just Chatting": ["just chatting", "irl", "react"],
    "Fortnite": ["fortnite", "fortnite clips", "battle royale"],
    "GTA V": ["gta", "gta 5", "gta rp", "grand theft auto"],
    "Minecraft": ["minecraft", "mc"],
    "Overwatch 2": ["overwatch", "overwatch 2", "ow2"],
    "Call of Duty": ["cod", "call of duty", "warzone"],
    "Apex Legends": ["apex", "apex legends"],
    "Counter-Strike": ["cs2", "counter strike", "csgo"],
}


def _slugify(text: str) -> str:
    """Convert text to a hashtag-safe slug."""
    return re.sub(r"[^a-zA-Z0-9]", "", text.lower())


def _build_tags(clip: dict, config: dict) -> list[str]:
    """Build an optimized tag list for YouTube search."""
    tags = []

    streamer = clip.get("streamer", "")
    game = clip.get("game", "")
    title = clip.get("title", "")

    # Streamer variations
    if streamer:
        tags.append(streamer)
        tags.append(f"{streamer} clips")
        tags.append(f"{streamer} twitch")
        tags.append(f"{streamer} best moments")

    # Game variations — expand known games
    if game:
        expansions = GAME_TAG_EXPANSIONS.get(game, [game.lower()])
        tags.extend(expansions)
        tags.append(f"{game} clips")
        tags.append(f"{game} funny moments")

    # Title keywords (strip short words)
    title_words = [w for w in title.split() if len(w) > 3]
    if title_words:
        tags.append(" ".join(title_words[:5]))

    # Platform
    platform = clip.get("platform", "")
    if platform:
        tags.append(f"{platform} clips")

    # Shorts
    if clip.get("is_shorts"):
        tags.append("Shorts")
        tags.append("YouTube Shorts")

    # Global tags from config
    global_tags = config.get("upload", {}).get("global_tags", [])
    tags.extend(global_tags)

    # Deduplicate, preserve order, cap at 500 chars total (YouTube limit)
    seen = set()
    unique_tags = []
    total_len = 0
    for tag in tags:
        tag = tag.strip()
        if not tag or tag.lower() in seen:
            continue
        seen.add(tag.lower())
        total_len += len(tag) + 1  # +1 for comma separator
        if total_len > 490:
            break
        unique_tags.append(tag)

    return unique_tags


def _build_title(clip: dict, config: dict) -> str:
    """Build an optimized title (max 100 chars, front-loaded keywords).

    Uses smart title generation to produce click-worthy titles from raw
    Twitch clip titles. Appends #Shorts for short-form content.
    """
    from clipper.process.titles import generate_title, sanitize_title

    result = generate_title(clip)

    # Safety net: sanitize again in case title was set externally
    sanitized = sanitize_title(result)
    if sanitized is None:
        streamer = clip.get("streamer", "Unknown").upper()
        game = clip.get("game", "")
        result = f"{streamer} {game} MOMENT" if game else f"{streamer} INSANE MOMENT"
    else:
        result = sanitized

    # Append #Shorts for short-form content — YouTube requires this
    is_shorts = clip.get("is_shorts", False)
    if is_shorts and "#Shorts" not in result:
        shorts_suffix = " #Shorts"
        max_base = 100 - len(shorts_suffix)
        if len(result) > max_base:
            result = result[:max_base - 3] + "..."
        result += shorts_suffix
    elif len(result) > 100:
        result = result[:97] + "..."

    return result


def _build_description(clip: dict, config: dict) -> str:
    """Build a keyword-rich description.

    For Shorts: prepends discovery hashtags (YouTube shows first 3 above the title).
    """
    template = config.get("upload", {}).get(
        "description_template",
        "{streamer} clip\n\n{url}"
    )

    streamer = clip.get("streamer", "Unknown")
    game = clip.get("game", "")

    format_vars = {
        "streamer": streamer,
        "title": clip.get("title", ""),
        "platform": clip.get("platform", ""),
        "url": clip.get("url", ""),
        "game": game,
        "game_tag": _slugify(game) if game else "gaming",
        "streamer_tag": _slugify(streamer),
        "tags_as_hashtags": " ".join(
            f"#{_slugify(t)}" for t in [streamer, game, "clips", "gaming"] if t
        ),
    }

    body = template.format(**format_vars)

    # For Shorts: prepend hashtags — YouTube displays the first 3 as clickable
    # pills above the title, which drives discovery
    if clip.get("is_shorts"):
        hashtags = []
        if game:
            hashtags.append(f"#{_slugify(game)}")
        if streamer:
            hashtags.append(f"#{_slugify(streamer)}")
        hashtags.append("#Shorts")
        hashtags.append("#gaming")
        # Cap at 4 — YouTube ignores descriptions with >15 hashtags
        body = " ".join(hashtags[:4]) + "\n\n" + body

    return body


def _find_best_scene_timestamp(video_path: str) -> float | None:
    """Find the most visually interesting frame using FFmpeg scene detection.

    Returns timestamp of the highest scene-change score (action moment).
    Falls back to 30% of duration if scene detection fails.
    """
    from clipper.config import get_ffprobe

    try:
        # Use scene detection to find frames with high visual change
        cmd = [
            get_ffprobe(),
            "-v", "quiet",
            "-show_entries", "frame=pts_time,score",
            "-select_streams", "v",
            "-of", "csv=p=0",
            "-f", "lavfi",
            f"movie={video_path},select='gt(scene\\,0.2)'",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.stdout.strip():
            # Parse scene timestamps and pick the one closest to the middle
            timestamps = []
            for line in result.stdout.strip().splitlines():
                parts = line.split(",")
                if parts and parts[0]:
                    try:
                        timestamps.append(float(parts[0]))
                    except ValueError:
                        continue

            if timestamps:
                # Prefer timestamps in the 20-60% range of the video
                probe = subprocess.run(
                    [get_ffprobe(), "-v", "quiet", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", video_path],
                    capture_output=True, text=True,
                )
                duration = float(probe.stdout.strip())
                target_start = duration * 0.2
                target_end = duration * 0.6

                # Filter to middle range, or use all if none in range
                mid_timestamps = [t for t in timestamps if target_start <= t <= target_end]
                if mid_timestamps:
                    return mid_timestamps[len(mid_timestamps) // 2]
                return timestamps[len(timestamps) // 2]
    except (subprocess.TimeoutExpired, Exception):
        pass

    return None


def _extract_thumbnail(video_path: str, output_path: str) -> str | None:
    """Extract the best thumbnail frame from the video.

    Uses scene detection to find the most visually interesting frame,
    then applies saturation + contrast boost for a more eye-catching thumbnail.
    Falls back to 30% of duration if scene detection fails.
    """
    from clipper.config import get_ffmpeg, get_ffprobe

    try:
        # Try scene detection first
        timestamp = _find_best_scene_timestamp(video_path)

        if timestamp is None:
            # Fallback: 30% into the video
            probe = subprocess.run(
                [get_ffprobe(), "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", video_path],
                capture_output=True, text=True,
            )
            duration = float(probe.stdout.strip())
            timestamp = duration * 0.3

        # Extract frame with saturation + contrast boost
        subprocess.run(
            [get_ffmpeg(), "-y", "-ss", str(timestamp), "-i", video_path,
             "-vframes", "1",
             "-vf", "eq=saturation=1.3:contrast=1.1",
             "-q:v", "2", output_path],
            capture_output=True, text=True, check=True,
        )
        return output_path
    except Exception:
        return None


def _ass_to_srt(ass_path: Path) -> Path:
    """Convert an ASS subtitle file to SRT format for YouTube caption upload."""
    import re as _re

    srt_path = ass_path.with_suffix(".srt")
    events = []

    with open(ass_path, encoding="utf-8-sig") as f:
        in_events = False
        for line in f:
            line = line.strip()
            if line == "[Events]":
                in_events = True
                continue
            if line.startswith("[") and in_events:
                break
            if in_events and line.startswith("Dialogue:"):
                # Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
                parts = line.split(",", 9)
                if len(parts) >= 10:
                    start = parts[1].strip()
                    end = parts[2].strip()
                    text = parts[9].strip()
                    # Strip ASS override tags like {\k50}, {\fad(...)}, etc.
                    text = _re.sub(r"\{[^}]*\}", "", text)
                    text = text.replace("\\N", "\n").replace("\\n", "\n").strip()
                    if text:
                        events.append((start, end, text))

    def _ass_time_to_srt(t: str) -> str:
        """Convert '0:00:05.20' to '00:00:05,200'."""
        parts = t.split(":")
        if len(parts) == 3:
            h, m, rest = parts
            if "." in rest:
                s, cs = rest.split(".")
                ms = int(cs.ljust(3, "0")[:3])
            else:
                s = rest
                ms = 0
            return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"
        return t

    with open(srt_path, "w", encoding="utf-8") as f:
        for i, (start, end, text) in enumerate(events, 1):
            f.write(f"{i}\n")
            f.write(f"{_ass_time_to_srt(start)} --> {_ass_time_to_srt(end)}\n")
            f.write(f"{text}\n\n")

    return srt_path


def _upload_captions(service, video_id: str, video_path: str, verbose: bool = False) -> None:
    """Upload subtitle file as a caption track if one exists alongside the video."""
    video_p = Path(video_path)

    # Look for subtitle files next to the video
    caption_path = None
    for ext in [".srt", ".ass"]:
        candidate = video_p.with_suffix(ext)
        if candidate.exists():
            if ext == ".ass":
                caption_path = _ass_to_srt(candidate)
            else:
                caption_path = candidate
            break

    # Also check for _final variants
    if caption_path is None:
        stem = video_p.stem.replace("_final", "")
        for ext in [".srt", ".ass"]:
            candidate = video_p.with_name(f"{stem}{ext}")
            if candidate.exists():
                if ext == ".ass":
                    caption_path = _ass_to_srt(candidate)
                else:
                    caption_path = candidate
                break

    if caption_path is None:
        return

    try:
        service.captions().insert(
            part="snippet",
            body={"snippet": {"videoId": video_id, "language": "en", "name": "English"}},
            media_body=MediaFileUpload(str(caption_path), mimetype="text/plain"),
        ).execute()
        if verbose:
            console.print(f"  [green]Captions uploaded:[/green] {caption_path.name}")
    except Exception as e:
        if verbose:
            console.print(f"  [yellow]Caption upload failed: {e}[/yellow]")


def publish_video(video_id: str, verbose: bool = False) -> bool:
    """Check if a video is done processing and flip it to public.

    Returns True if the video was published, False if still processing or error.
    """
    try:
        service = get_youtube_service()
        response = service.videos().list(
            part="status,processingDetails",
            id=video_id,
        ).execute()

        items = response.get("items", [])
        if not items:
            console.print(f"[red]Video {video_id} not found.[/red]")
            return False

        video = items[0]
        status = video.get("status", {})
        upload_status = status.get("uploadStatus", "")

        if upload_status != "processed":
            if verbose:
                console.print(f"  [yellow]Video still processing ({upload_status}).[/yellow]")
            return False

        if status.get("privacyStatus") == "public":
            if verbose:
                console.print(f"  [dim]Already public.[/dim]")
            return True

        service.videos().update(
            part="status",
            body={
                "id": video_id,
                "status": {"privacyStatus": "public"},
            },
        ).execute()
        console.print(f"[green]Published:[/green] https://youtube.com/watch?v={video_id}")
        return True

    except Exception as e:
        console.print(f"[red]Publish error: {e}[/red]")
        return False


def upload_clip(clip, config, privacy="unlisted", verbose=False):
    """Upload a processed clip to YouTube with optimized metadata.

    Args:
        clip: Dict with title, streamer, url, game, platform, processed_path, is_shorts.
        config: Loaded config dict.
        privacy: YouTube privacy status (unlisted, public, private).
        verbose: Whether to print extra info.

    Returns:
        Video ID string on success, None on failure.
    """
    processed_path = clip.get("processed_path")
    if not processed_path or not Path(processed_path).exists():
        console.print(f"[red]Missing processed file: {processed_path}[/red]")
        return None

    title = clip.get("_title_override") or _build_title(clip, config)
    description = clip.get("_description_override") or _build_description(clip, config)
    tags = _build_tags(clip, config)

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "20",  # Gaming
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(processed_path, resumable=True)

    if verbose:
        console.print(f"[dim]Title: {title}[/dim]")
        console.print(f"[dim]Tags ({len(tags)}): {', '.join(tags[:10])}...[/dim]")
        console.print(f"[dim]Privacy: {privacy}[/dim]")

    console.print(f"  [bold]Title:[/bold] {title}")
    console.print(f"  [bold]Tags:[/bold] {len(tags)} tags")

    try:
        service = get_youtube_service()
        request = service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status and verbose:
                console.print(f"[dim]Upload progress: {int(status.progress() * 100)}%[/dim]")

        video_id = response["id"]

        # Upload captions if subtitle file exists
        _upload_captions(service, video_id, processed_path, verbose=verbose)

        # Set thumbnail if configured
        if config.get("upload", {}).get("extract_thumbnail", False):
            thumb_path = Path(processed_path).with_suffix(".jpg")
            thumb = _extract_thumbnail(processed_path, str(thumb_path))
            if thumb:
                try:
                    service.thumbnails().set(
                        videoId=video_id,
                        media_body=MediaFileUpload(thumb, mimetype="image/jpeg"),
                    ).execute()
                    console.print("  [green]Custom thumbnail set[/green]")
                except Exception as e:
                    # Custom thumbnails require verified account
                    if verbose:
                        console.print(f"  [yellow]Thumbnail upload failed (may need verified account): {e}[/yellow]")

        return video_id

    except Exception as e:
        error_msg = str(e)
        if "quotaExceeded" in error_msg:
            console.print("[red]YouTube API quota exceeded. Try again tomorrow.[/red]")
        else:
            console.print(f"[red]Upload error: {error_msg}[/red]")
        return None
