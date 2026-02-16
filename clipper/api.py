"""FastAPI backend for the Clipper web dashboard."""

import json
import logging
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from clipper.config import get_ffprobe, load_config
from clipper.pipeline_state import PipelineState


# -- Request/Response models --


class WorkflowStartRequest(BaseModel):
    recipe: str  # "shorts" | "compilation"
    game: str
    count: int = 5
    channel: str | None = None


class UploadRequest(BaseModel):
    clip_id: str
    privacy: str = "unlisted"
    channel: str | None = None


class BatchUploadRequest(BaseModel):
    clip_ids: list[str]
    privacy: str = "unlisted"
    channel: str | None = None


class ClipUpdateRequest(BaseModel):
    title_override: str | None = None
    description_override: str | None = None
    tags_override: list[str] | None = None
    sync_youtube: bool = False


class FacecamRectRequest(BaseModel):
    x: float
    y: float
    w: float
    h: float


class LayoutProfileRequest(BaseModel):
    # Preferred: nested rect objects
    facecam: FacecamRectRequest | None = None
    hud: FacecamRectRequest | None = None
    facecam_enabled: bool | None = None
    hud_enabled: bool | None = None
    safe_top_ratio: float | None = None
    safe_bottom_ratio: float | None = None
    facecam_band_ratio: float | None = None
    facecam_x_bias: float | None = None
    facecam_y_bias: float | None = None
    facecam_zoom: float | None = None
    gameplay_zoom: float | None = None
    gameplay_zoom_no_facecam: float | None = None
    gameplay_x_bias: float | None = None
    gameplay_y_bias: float | None = None
    hud_height_ratio: float | None = None
    hud_scale: float | None = None
    hud_x_ratio: float | None = None
    hud_y_ratio: float | None = None
    title_y_ratio: float | None = None
    subtitle_margin_ratio: float | None = None

    # Back-compat: accept a raw rect as the facecam crop
    x: float | None = None
    y: float | None = None
    w: float | None = None
    h: float | None = None


class AutopilotStartRequest(BaseModel):
    count: int = 5
    min_score: int = 40
    channel: str | None = None


class FetchScoreRequest(BaseModel):
    game: str
    channel: str | None = None
    period: str | None = None
    fetch_scope: str | None = None
    streamers: list[str] | None = None


class ApproveProcessRequest(BaseModel):
    clip_ids: list[str]
    recipe: str  # "shorts" | "compilation"
    channel: str | None = None
    shorts_layout: str | None = None
    layout_overrides: dict[str, dict] | None = None


class CompilationBuildRequest(BaseModel):
    clip_ids: list[str]
    title: str | None = None
    countdown: bool = True
    channel: str | None = None


class PublishRequest(BaseModel):
    video_ids: list[str]


class ReleaseRequest(BaseModel):
    clip_id: str
    channel: str
    scheduled_at: str  # ISO datetime


# -- App factory --


def _resolve_clip_meta(output_dir: Path, clip_id: str) -> Path:
    """Find clip metadata JSON by exact name or substring match."""
    meta_path = output_dir / f"{clip_id}.json"
    if meta_path.exists():
        return meta_path
    for p in output_dir.glob("*.json"):
        if clip_id in p.stem:
            return p
    raise HTTPException(404, f"Clip {clip_id} not found")


def _resolve_output_video_path(output_dir: Path, clip_id: str) -> Path:
    """Resolve a clip_id to an existing output video path."""
    try:
        meta_path = _resolve_clip_meta(output_dir, clip_id)
        data = json.loads(meta_path.read_text())
        processed = Path(data.get("processed_path", ""))
        if processed.exists():
            return processed
    except Exception:
        pass

    exact_mp4 = output_dir / f"{clip_id}.mp4"
    if exact_mp4.exists():
        return exact_mp4

    for candidate in output_dir.glob("*.mp4"):
        if clip_id in candidate.stem:
            return candidate

    raise HTTPException(404, f"Video file not found for clip {clip_id}")


def _open_path(path: Path, reveal: bool = False) -> None:
    """Open a path in the OS file manager."""
    try:
        if sys.platform == "darwin":
            cmd = ["open", "-R", str(path)] if reveal else ["open", str(path)]
            subprocess.run(cmd, check=True, timeout=10)
            return

        if sys.platform.startswith("win"):
            if reveal:
                subprocess.run(["explorer", "/select,", str(path)], check=True, timeout=10)
            else:
                subprocess.run(["explorer", str(path)], check=True, timeout=10)
            return

        opener = shutil.which("xdg-open")
        if opener:
            target = path.parent if reveal else path
            subprocess.run([opener, str(target)], check=True, timeout=10)
            return
    except Exception as e:
        raise HTTPException(500, f"Failed to open path: {e}")

    raise HTTPException(500, "No supported file-manager opener found on this system")


def _looks_publishable_video(path: Path) -> bool:
    """Heuristic: include final publishable assets, not intermediate transcodes."""
    stem = path.stem.lower()
    if stem.startswith("compilation_"):
        return True
    return stem.endswith("_final") or stem.endswith("_shorts")


def _probe_duration_seconds(video_path: Path) -> float:
    """Get video duration (seconds), returning 0 on probe failure."""
    try:
        probe = subprocess.run(
            [
                get_ffprobe(),
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=True,
        )
        return round(float(probe.stdout.strip()), 2)
    except Exception:
        return 0.0


def _synthesize_output_meta(video_path: Path) -> dict:
    """Create minimal metadata for orphan output videos lacking JSON metadata."""
    clip_id = video_path.stem
    is_compilation = clip_id.lower().startswith("compilation_")
    stem_parts = [p for p in re.split(r"[_-]+", clip_id) if p]
    streamer_guess = stem_parts[0] if len(stem_parts) >= 2 else "Unknown"
    game_guess = stem_parts[1] if len(stem_parts) >= 3 else "Unknown"
    title_guess = clip_id.replace("_", " ").strip()
    if title_guess:
        title_guess = title_guess[0].upper() + title_guess[1:]

    return {
        "id": clip_id,
        "title": title_guess or clip_id,
        "streamer": streamer_guess,
        "game": game_guess,
        "platform": "twitch",
        "url": "",
        "duration": _probe_duration_seconds(video_path),
        "view_count": 0,
        "processed_path": str(video_path),
        "is_shorts": False if is_compilation else True,
        "clip_count": 0 if not is_compilation else None,
        "_orphan": True,
    }


def _build_local_trending_fallback(config: dict) -> list[dict]:
    """Build a local trending list when Twitch credentials/API are unavailable."""
    from clipper.db import get_db

    games_seed = config.get("targets", {}).get("twitch", {}).get("games", []) or []
    by_game: dict[str, dict] = {}

    for game_name in games_seed:
        key = str(game_name).strip().lower()
        if not key:
            continue
        by_game[key] = {
            "game_id": key.replace(" ", "_"),
            "game_name": str(game_name).strip(),
            "platform": "Local",
            "clip_count": 0,
            "total_views": 0,
            "avg_views": 0,
        }

    # Aggregate from DB
    conn = get_db(config)
    rows = conn.execute(
        "SELECT LOWER(game) as game_key, game, COUNT(*) as cnt, SUM(view_count) as total_views "
        "FROM clips WHERE game IS NOT NULL AND game != '' AND id NOT LIKE 'compilation_%' "
        "GROUP BY game_key"
    ).fetchall()

    for r in rows:
        key = r["game_key"]
        row = by_game.setdefault(
            key,
            {
                "game_id": key.replace(" ", "_"),
                "game_name": r["game"],
                "platform": "Local",
                "clip_count": 0,
                "total_views": 0,
                "avg_views": 0,
            },
        )
        row["clip_count"] += r["cnt"]
        row["total_views"] += r["total_views"] or 0

    result = list(by_game.values())
    for row in result:
        count = int(row["clip_count"])
        row["avg_views"] = int(row["total_views"] / count) if count > 0 else 0

    result.sort(key=lambda g: (g["total_views"], g["clip_count"], g["game_name"]), reverse=True)
    return result[:20]


def create_app(config: dict | None = None) -> FastAPI:
    if config is None:
        config = load_config()

    app = FastAPI(title="Clipper API")
    state = PipelineState()
    workflow_thread: dict[str, threading.Thread | None] = {"current": None}
    shutdown_event = threading.Event()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- Endpoints --

    @app.get("/api/config")
    def get_config():
        safe = {
            k: v for k, v in config.items()
            if not k.startswith("_") and not isinstance(v, Path)
        }
        return safe

    # -- Shorts Layout Profiles --

    @app.get("/api/layout/facecam-profiles")
    def get_facecam_profiles():
        from clipper.layout_profiles import load_facecam_profiles

        return {"profiles": load_facecam_profiles(config)}

    @app.put("/api/layout/facecam-profiles/{streamer}")
    def put_facecam_profile(streamer: str, profile: LayoutProfileRequest):
        from clipper.layout_profiles import upsert_layout_profile

        facecam = profile.facecam.model_dump() if profile.facecam is not None else None
        hud = profile.hud.model_dump() if profile.hud is not None else None
        facecam_enabled = profile.facecam_enabled
        hud_enabled = profile.hud_enabled
        layout_tuning = {
            "safe_top_ratio": profile.safe_top_ratio,
            "safe_bottom_ratio": profile.safe_bottom_ratio,
            "facecam_band_ratio": profile.facecam_band_ratio,
            "facecam_x_bias": profile.facecam_x_bias,
            "facecam_y_bias": profile.facecam_y_bias,
            "facecam_zoom": profile.facecam_zoom,
            "gameplay_zoom": profile.gameplay_zoom,
            "gameplay_zoom_no_facecam": profile.gameplay_zoom_no_facecam,
            "gameplay_x_bias": profile.gameplay_x_bias,
            "gameplay_y_bias": profile.gameplay_y_bias,
            "hud_height_ratio": profile.hud_height_ratio,
            "hud_scale": profile.hud_scale,
            "hud_x_ratio": profile.hud_x_ratio,
            "hud_y_ratio": profile.hud_y_ratio,
            "title_y_ratio": profile.title_y_ratio,
            "subtitle_margin_ratio": profile.subtitle_margin_ratio,
        }
        layout_tuning = {k: v for k, v in layout_tuning.items() if v is not None}

        # Back-compat: {x,y,w,h} means "facecam rect"
        if facecam is None and all(getattr(profile, k) is not None for k in ("x", "y", "w", "h")):
            facecam = {"x": profile.x, "y": profile.y, "w": profile.w, "h": profile.h}

        if facecam is None and hud is None and facecam_enabled is None and hud_enabled is None and not layout_tuning:
            raise HTTPException(400, "Expected facecam/hud rect, enabled flags, or layout tuning")

        updated = upsert_layout_profile(
            config,
            streamer,
            facecam=facecam,
            hud=hud,
            facecam_enabled=facecam_enabled,
            hud_enabled=hud_enabled,
            layout_tuning=layout_tuning if layout_tuning else None,
        )
        return {"ok": True, "streamer": streamer, "profile": updated}

    @app.delete("/api/layout/facecam-profiles/{streamer}")
    def delete_facecam_profile(streamer: str):
        from clipper.layout_profiles import delete_facecam_profile as _delete

        removed = _delete(config, streamer)
        return {"ok": True, "removed": removed}

    @app.post("/api/review/batch")
    def review_batch(body: dict):
        from clipper.db import update_clip as db_update_clip

        clip_ids = body.get("clip_ids", [])
        action = body.get("action", "approve")
        status = "approved" if action == "approve" else "skipped"
        for clip_id in clip_ids:
            db_update_clip(config, clip_id, status=status)
        return {"updated": len(clip_ids), "status": status}

    @app.get("/api/queue")
    def get_queue(
        status: str = Query("pending"),
        game: str = Query(""),
        streamer: str = Query(""),
        channel: str = Query("all"),
        sort: str = Query(""),
        include_compilations: bool = Query(False),
        include_orphans: bool = Query(True),
        limit: int = Query(250, ge=1, le=2000),
    ):
        from clipper.db import list_clips

        if status not in ("pending", "approved", "output"):
            raise HTTPException(400, f"Unknown status: {status}")

        channel_key = str(channel or "").strip()
        if channel_key.lower() in ("", "all", "*"):
            channel_key = ""

        sort_key = sort if sort else ("recent" if status == "output" else "fetched_at")

        clips = list_clips(
            config,
            status=status,
            game=game if game else None,
            channel=channel_key if channel_key else None,
            streamer=streamer if streamer else None,
            sort=sort_key,
            limit=limit,
            exclude_compilations=(status == "output" and not include_compilations),
        )

        # For output clips, verify processed_path still exists
        if status == "output":
            clips = [c for c in clips if c.get("processed_path") and Path(c["processed_path"]).exists()]

        # Orphan output videos (MP4 files with no DB entry)
        if status == "output" and include_orphans and len(clips) < limit:
            output_dir = config["_output_dir"]
            if output_dir.exists():
                existing_ids = {str(c.get("id", "")).strip() for c in clips}
                for mp4 in sorted(output_dir.glob("*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True):
                    if len(clips) >= limit:
                        break
                    if not _looks_publishable_video(mp4):
                        continue
                    clip_id = mp4.stem
                    if clip_id in existing_ids:
                        continue
                    clip = _synthesize_output_meta(mp4)
                    if not include_compilations and ("compilation" in clip_id or clip.get("clip_count")):
                        continue
                    if game and game.lower() not in clip.get("game", "").lower():
                        continue
                    if streamer and streamer.lower() not in clip.get("streamer", "").lower():
                        continue
                    if channel_key:
                        continue
                    clips.append(clip)

        if status == "output":
            from clipper.learn import get_learned_weights
            from clipper.process.score import score_clip

            weights = get_learned_weights(config)
            for clip in clips:
                if "_score" in clip:
                    continue
                try:
                    clip["_score"] = round(score_clip(clip, weights=weights), 1)
                except Exception:
                    clip["_score"] = 0

        if status == "output":
            from clipper.upload.youtube import _build_description, _build_tags, _build_title

            for clip in clips:
                try:
                    clip["_generated_title"] = _build_title(clip, config)
                    clip["_generated_description"] = _build_description(clip, config)
                    clip["_generated_tags"] = _build_tags(clip, config)
                except Exception:
                    continue

        return {"clips": clips, "limit": limit}

    @app.post("/api/output/resync")
    def resync_output(limit: int = Query(200, ge=1, le=1000)):
        """Create metadata JSON records for publishable orphan output videos."""
        output_dir = config["_output_dir"]
        if not output_dir.exists():
            return {"created": 0, "clips": []}

        created: list[str] = []
        orphans = [
            p for p in output_dir.glob("*.mp4")
            if _looks_publishable_video(p) and not (output_dir / f"{p.stem}.json").exists()
        ]
        orphans.sort(key=lambda x: x.stat().st_mtime, reverse=True)

        for mp4 in orphans[:limit]:
            clip = _synthesize_output_meta(mp4)
            meta_path = output_dir / f"{mp4.stem}.json"
            try:
                meta_path.write_text(json.dumps(clip, indent=2))
                created.append(mp4.stem)
            except OSError as e:
                logger.warning("Failed writing orphan metadata for %s: %s", mp4.name, e)

        return {"created": len(created), "clips": created, "orphans_found": len(orphans)}

    @app.post("/api/output/open-folder")
    def open_output_folder():
        output_dir = config["_output_dir"]
        if not output_dir.exists():
            raise HTTPException(404, "Output folder not found")
        _open_path(output_dir, reveal=False)
        return {"ok": True, "path": str(output_dir)}

    @app.post("/api/output/open/{clip_id}")
    def open_output_clip(clip_id: str):
        output_dir = config["_output_dir"]
        video_path = _resolve_output_video_path(output_dir, clip_id)
        _open_path(video_path, reveal=True)
        return {"ok": True, "path": str(video_path)}

    @app.get("/api/video/{clip_id}")
    def get_video(clip_id: str):
        output_dir = config["_output_dir"]

        # Try metadata-based resolution first
        try:
            meta_path = _resolve_clip_meta(output_dir, clip_id)
            data = json.loads(meta_path.read_text())
            video_path = data.get("processed_path")
            if video_path and Path(video_path).exists():
                return FileResponse(video_path, media_type="video/mp4")
        except (HTTPException, json.JSONDecodeError, OSError):
            pass

        # Fallback: find video file directly
        video_path = _resolve_output_video_path(output_dir, clip_id)
        return FileResponse(str(video_path), media_type="video/mp4")

    @app.post("/api/workflow/start")
    def workflow_start(req: WorkflowStartRequest):
        if workflow_thread["current"] and workflow_thread["current"].is_alive():
            raise HTTPException(409, "A workflow is already running")

        # Reset state for new run
        state.reset(recipe=req.recipe, phase="starting", detail=f"{req.recipe} — {req.game}")

        def _run():
            try:
                if req.recipe == "shorts":
                    from clipper.workflow import run_shorts_workflow
                    run_shorts_workflow(
                        config, game=req.game, count=req.count,
                        channel=req.channel, state=state,
                    )
                elif req.recipe == "compilation":
                    from clipper.workflow import run_compilation_workflow
                    run_compilation_workflow(
                        config, game=req.game, duration=None,
                        channel=req.channel, state=state,
                    )
                if state.phase not in ("error",):
                    state.set_phase("done", f"{state.completed} clips processed")
            except Exception as e:
                state.set_error(str(e))

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        workflow_thread["current"] = t

        return {"status": "started", "recipe": req.recipe, "game": req.game}

    @app.post("/api/workflow/fetch-score")
    def workflow_fetch_score(req: FetchScoreRequest):
        """Synchronous: fetch clips for a game, score them, return tiers."""
        from clipper.workflow import _fetch_clips, _load_and_score_pending
        from clipper.db import get_db

        try:
            fetched = _fetch_clips(
                config,
                req.game,
                period=req.period or "24h",
                scope=req.fetch_scope or "gamewide",
                streamers=req.streamers or [],
            )
        except ValueError as e:
            raise HTTPException(400, str(e))

        # Reset the review queue to only show clips from this fetch batch.
        # Step 1: skip ALL currently pending clips (clean slate).
        # Step 2: set all fetched clips to pending (re-activate even if previously skipped).
        fetched_ids = {str(c.get("id", "")).strip() for c in (fetched or []) if c.get("id")}

        conn = get_db(config)
        conn.execute("UPDATE clips SET status = 'skipped' WHERE status = 'pending'")
        if fetched_ids:
            id_list = list(fetched_ids)
            placeholders = ",".join("?" for _ in id_list)
            conn.execute(
                f"UPDATE clips SET status = 'pending' WHERE id IN ({placeholders}) AND status = 'skipped'",
                id_list,
            )
        conn.commit()

        if not fetched:
            return {"clips": [], "tiers": [], "clip_count": 0}
        pending = _load_and_score_pending(config, game=req.game)

        # Keep this fetch isolated from stale queue entries.
        fetched_ids = {str(c.get("id", "")).strip() for c in (fetched or []) if c.get("id")}
        if fetched_ids:
            pending = [c for c in pending if str(c.get("id", "")).strip() in fetched_ids]

        scope_key = str(req.fetch_scope or "").strip().lower()
        if scope_key in ("selected", "configured"):
            if scope_key == "selected":
                allow = {str(s).strip().lower() for s in (req.streamers or []) if str(s).strip()}
            else:
                allow = {
                    str(s).strip().lower()
                    for s in (config.get("targets", {}).get("twitch", {}).get("streamers", []) or [])
                    if str(s).strip()
                }
            if allow:
                pending = [c for c in pending if str(c.get("streamer", "")).strip().lower() in allow]

        if not pending:
            return {"clips": [], "tiers": [], "clip_count": 0}

        from clipper.process.tiers import compute_duration_tiers
        tiers = compute_duration_tiers(pending)
        # Round for JSON response
        for t in tiers:
            t["avg_score"] = round(t["avg_score"], 1)
            t["actual_min"] = round(t["actual_min"], 1)

        # Strip _path from clips before sending to frontend
        safe_clips = []
        for c in pending:
            clip_copy = {k: v for k, v in c.items() if k != "_path"}
            safe_clips.append(clip_copy)

        return {"clips": safe_clips, "tiers": tiers, "clip_count": len(pending)}

    @app.post("/api/workflow/approve-process")
    def workflow_approve_process(req: ApproveProcessRequest):
        """Mark selected clips as approved, start processing in background."""
        if workflow_thread["current"] and workflow_thread["current"].is_alive():
            raise HTTPException(409, "A workflow is already running")

        from clipper.db import get_db, update_clip as db_update_clip

        channel_key = (req.channel or "").strip() or None
        channels_cfg = config.get("channels", {}) or {}
        if channel_key and channel_key not in channels_cfg:
            raise HTTPException(400, f"Unknown channel: {channel_key}")

        layout_key = str(req.shorts_layout or "").strip().lower()
        if layout_key in ("", "default", "none"):
            layout_key = ""
        if layout_key and layout_key not in ("blur", "fill"):
            raise HTTPException(400, f"Unknown shorts_layout: {layout_key}")

        conn = get_db(config)

        # Reset any previously approved clips back to skipped
        conn.execute("UPDATE clips SET status = 'skipped' WHERE status = 'approved'")
        conn.commit()

        selected_ids = set(req.clip_ids)
        layout_overrides = req.layout_overrides if isinstance(req.layout_overrides, dict) else {}
        approved_count = 0

        for clip_id in selected_ids:
            updates: dict = {"status": "approved"}
            if channel_key:
                updates["channel"] = channel_key
            if layout_key:
                updates["_shorts_layout"] = layout_key
            db_update_clip(config, clip_id, **updates)
            approved_count += 1

        # Skip remaining pending
        conn.execute("UPDATE clips SET status = 'skipped' WHERE status = 'pending'")
        conn.commit()

        if approved_count == 0:
            raise HTTPException(400, "No clips matched the provided IDs")

        # Reset state
        state.reset(recipe=req.recipe, phase="processing", detail=f"Processing {approved_count} clips")

        for_compilation = req.recipe == "compilation"

        def _run():
            try:
                from clipper.workflow import _process_clips
                _process_clips(config, for_compilation=for_compilation, state=state)
                if state.phase != "error":
                    state.set_phase("done", f"{state.completed} clips processed")
            except Exception as e:
                state.set_error(str(e))

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        workflow_thread["current"] = t

        return {"status": "started", "approved": approved_count, "recipe": req.recipe}

    @app.get("/api/workflow/stream")
    def workflow_stream():
        def _generate():
            idle_count = 0
            while not shutdown_event.is_set():
                snap = state.snapshot()
                thread = workflow_thread.get("current")
                snap["running"] = thread is not None and thread.is_alive()
                yield f"data: {json.dumps(snap)}\n\n"
                if snap["running"] or snap["phase"] in ("done", "error"):
                    idle_count = 0
                    shutdown_event.wait(0.25)
                else:
                    # Idle — send heartbeats less frequently
                    idle_count += 1
                    shutdown_event.wait(min(2.0, 0.5 * idle_count))

        return StreamingResponse(
            _generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.on_event("shutdown")
    def on_shutdown():
        shutdown_event.set()

    @app.post("/api/upload")
    def upload_single(req: UploadRequest):
        from clipper.upload.youtube import upload_clip

        output_dir = config["_output_dir"]
        meta_path = _resolve_clip_meta(output_dir, req.clip_id)

        clip = json.loads(meta_path.read_text())
        channels_cfg = config.get("channels", {}) or {}
        if req.channel and req.channel not in channels_cfg:
            raise HTTPException(400, f"Unknown channel: {req.channel}")

        if req.channel:
            channel_order: list[str | None] = [req.channel]
        elif channels_cfg:
            # Auto-route uploads across configured channels when no specific channel is chosen.
            channel_order = list(channels_cfg.keys())
            if "default" in channels_cfg:
                channel_order = ["default"] + [c for c in channel_order if c != "default"]
        else:
            channel_order = [None]

        video_id = None
        used_channel = None
        attempts: list[dict] = []
        for channel_name in channel_order:
            video_id = upload_clip(clip, config, privacy=req.privacy, channel=channel_name)
            reason = clip.pop("_upload_error_reason", None)
            message = clip.pop("_upload_error_message", None)
            status_code = clip.pop("_upload_error_status", None)
            if not video_id:
                attempts.append(
                    {
                        "channel": channel_name or "default",
                        "reason": reason,
                        "status": status_code,
                        "message": message,
                    }
                )
                # Project-level quota won't succeed on another channel.
                if reason == "quotaExceeded":
                    break
            if video_id:
                used_channel = channel_name
                break

        if video_id:
            from clipper.db import update_clip as db_update_clip
            clip["video_id"] = video_id
            if used_channel:
                clip["channel"] = used_channel
                clip.setdefault("_target_channel", used_channel)
            meta_path.write_text(json.dumps(clip, indent=2))
            db_update_clip(config, req.clip_id, video_id=video_id, channel=used_channel or "")
            return {"video_id": video_id, "channel": used_channel, "attempts": attempts}

        attempted = ", ".join([c for c in channel_order if c]) or "default OAuth token"
        raise HTTPException(
            500,
            f"Upload failed (attempted: {attempted}). "
            + (f"Last error: {attempts[-1].get('reason') or attempts[-1].get('message')}" if attempts else ""),
        )

    @app.post("/api/upload/batch")
    def upload_batch(req: BatchUploadRequest):
        from clipper.upload.youtube import upload_clip

        output_dir = config["_output_dir"]
        channels_cfg = config.get("channels", {}) or {}
        if req.channel and req.channel not in channels_cfg:
            raise HTTPException(400, f"Unknown channel: {req.channel}")

        if req.channel:
            channel_order: list[str | None] = [req.channel]
        elif channels_cfg:
            channel_order = list(channels_cfg.keys())
            if "default" in channels_cfg:
                channel_order = ["default"] + [c for c in channel_order if c != "default"]
        else:
            channel_order = [None]

        results = []
        for clip_id in req.clip_ids:
            try:
                meta_path = _resolve_clip_meta(output_dir, clip_id)
            except HTTPException:
                results.append({"clip_id": clip_id, "error": "not found"})
                continue

            clip = json.loads(meta_path.read_text())
            video_id = None
            used_channel = None
            attempts: list[dict] = []
            for channel_name in channel_order:
                video_id = upload_clip(clip, config, privacy=req.privacy, channel=channel_name)
                reason = clip.pop("_upload_error_reason", None)
                message = clip.pop("_upload_error_message", None)
                status_code = clip.pop("_upload_error_status", None)
                if not video_id:
                    attempts.append(
                        {
                            "channel": channel_name or "default",
                            "reason": reason,
                            "status": status_code,
                            "message": message,
                        }
                    )
                    if reason == "quotaExceeded":
                        break
                if video_id:
                    used_channel = channel_name
                    break

            if video_id:
                from clipper.db import update_clip as db_update_clip
                clip["video_id"] = video_id
                if used_channel:
                    clip["channel"] = used_channel
                    clip.setdefault("_target_channel", used_channel)
                meta_path.write_text(json.dumps(clip, indent=2))
                db_update_clip(config, clip_id, video_id=video_id, channel=used_channel or "")
                results.append({"clip_id": clip_id, "video_id": video_id, "channel": used_channel, "attempts": attempts})
            else:
                attempted = ", ".join([c for c in channel_order if c]) or "default OAuth token"
                last = attempts[-1] if attempts else {}
                detail = last.get("reason") or last.get("message") or "upload failed"
                results.append({"clip_id": clip_id, "error": f"{detail} (attempted: {attempted})", "attempts": attempts})

        return {"results": results}

    @app.patch("/api/clips/{clip_id}")
    def update_clip(clip_id: str, req: ClipUpdateRequest):
        from clipper.db import update_clip as db_update_clip, get_clip

        output_dir = config["_output_dir"]
        updates: dict = {}
        if req.title_override is not None:
            updates["title_override"] = req.title_override
        if req.description_override is not None:
            updates["description_override"] = req.description_override
        if req.tags_override is not None:
            updates["tags_override"] = json.dumps(req.tags_override)

        if updates:
            db_update_clip(config, clip_id, **updates)

        # Also update the JSON file for backward compat (upload reads it)
        meta_path = output_dir / f"{clip_id}.json"
        if meta_path.exists():
            clip = json.loads(meta_path.read_text())
            if req.title_override is not None:
                clip["_title_override"] = req.title_override
            if req.description_override is not None:
                clip["_description_override"] = req.description_override
            if req.tags_override is not None:
                clip["_tags_override"] = req.tags_override
            meta_path.write_text(json.dumps(clip, indent=2))
        else:
            clip = get_clip(config, clip_id) or {}

        # Sync to YouTube if requested and clip has been uploaded
        if req.sync_youtube and clip.get("video_id"):
            from clipper.upload.youtube import update_video_metadata
            ok = update_video_metadata(
                clip["video_id"],
                title=req.title_override,
                description=req.description_override,
                tags=req.tags_override,
                channel=clip.get("channel"),
                config=config,
            )
            if not ok:
                raise HTTPException(502, "Saved locally but YouTube sync failed")

        return {"ok": True}

    @app.get("/api/clips/{clip_id}/subtitles")
    def get_subtitles(clip_id: str):
        """Parse ASS subtitle file and return structured lines."""
        from clipper.db import get_clip as db_get_clip

        clip = db_get_clip(config, clip_id)
        if not clip:
            raise HTTPException(404, f"Clip {clip_id} not found")

        ass_path_str = clip.get("_subtitle_path")
        if not ass_path_str:
            # Try to find .ass next to processed video
            processed = clip.get("processed_path", "")
            if processed:
                candidate = Path(processed).with_suffix(".ass")
                if candidate.exists():
                    ass_path_str = str(candidate)

        if not ass_path_str or not Path(ass_path_str).exists():
            raise HTTPException(404, f"No subtitle file for clip {clip_id}")

        ass_path = Path(ass_path_str)
        lines = []
        idx = 0
        for raw_line in ass_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.startswith("Dialogue:"):
                continue
            # Format: Dialogue: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
            parts = raw_line.split(",", 9)
            if len(parts) < 10:
                continue
            start = parts[1].strip()
            end = parts[2].strip()
            raw_text = parts[9]
            # Strip ASS override tags for display
            plain = re.sub(r"\{[^}]*\}", "", raw_text).replace("\\N", " ").strip()
            lines.append({
                "index": idx,
                "start": start,
                "end": end,
                "text": plain,
                "raw": raw_text,
            })
            idx += 1

        return {"lines": lines, "ass_path": str(ass_path)}

    @app.put("/api/clips/{clip_id}/subtitles")
    def update_subtitles(clip_id: str, body: dict):
        """Update subtitle lines. Regenerates ASS file preserving header."""
        from clipper.db import get_clip as db_get_clip

        clip = db_get_clip(config, clip_id)
        if not clip:
            raise HTTPException(404, f"Clip {clip_id} not found")

        ass_path_str = clip.get("_subtitle_path")
        if not ass_path_str:
            processed = clip.get("processed_path", "")
            if processed:
                candidate = Path(processed).with_suffix(".ass")
                if candidate.exists():
                    ass_path_str = str(candidate)

        if not ass_path_str or not Path(ass_path_str).exists():
            raise HTTPException(404, f"No subtitle file for clip {clip_id}")

        ass_path = Path(ass_path_str)
        updated_lines = body.get("lines", [])

        # Read existing file, keep everything before [Events] dialogue lines
        original = ass_path.read_text(encoding="utf-8")
        header_lines = []
        in_events = False
        for line in original.splitlines():
            if line.startswith("Dialogue:"):
                in_events = True
                continue
            header_lines.append(line)

        # Rebuild dialogue lines from updated data
        dialogue_lines = []
        for item in updated_lines:
            start = item.get("start", "0:00:00.00")
            end = item.get("end", "0:00:00.00")
            raw = item.get("raw", "")
            text = item.get("text", "")
            # If raw is present, use it (preserves ASS tags); otherwise use plain text
            content = raw if raw else text.upper()
            dialogue_lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{content}")

        output = "\n".join(header_lines) + "\n" + "\n".join(dialogue_lines) + "\n"
        ass_path.write_text(output, encoding="utf-8")

        return {"ok": True, "lines_written": len(dialogue_lines)}

    @app.get("/api/clips/{clip_id}/title-preview")
    def get_title_preview(clip_id: str):
        from clipper.upload.youtube import _build_title

        output_dir = config["_output_dir"]
        meta_path = _resolve_clip_meta(output_dir, clip_id)

        clip = json.loads(meta_path.read_text())
        title = _build_title(clip, config)
        return {"title": title}

    @app.get("/api/clips/{clip_id}/tags-preview")
    def get_tags_preview(clip_id: str):
        from clipper.upload.youtube import _build_tags

        output_dir = config["_output_dir"]
        meta_path = _resolve_clip_meta(output_dir, clip_id)

        clip = json.loads(meta_path.read_text())
        tags = _build_tags(clip, config)
        return {"tags": tags}

    @app.get("/api/clips/{clip_id}/description-preview")
    def get_description_preview(clip_id: str):
        from clipper.upload.youtube import _build_description

        output_dir = config["_output_dir"]
        meta_path = _resolve_clip_meta(output_dir, clip_id)

        clip = json.loads(meta_path.read_text())
        description = _build_description(clip, config)
        return {"description": description}

    @app.post("/api/publish")
    def publish_videos(req: PublishRequest):
        from clipper.upload.youtube import publish_video

        results = []
        for vid in req.video_ids:
            ok = publish_video(vid)
            results.append({"video_id": vid, "published": ok})
        return {"results": results}

    @app.post("/api/clips/{clip_id}/publish")
    def publish_clip(clip_id: str):
        """Publish a specific clip using the same channel it was uploaded with."""
        output_dir = config["_output_dir"]
        meta_path = _resolve_clip_meta(output_dir, clip_id)
        clip = json.loads(meta_path.read_text())

        video_id = clip.get("video_id")
        if not video_id or video_id == "previously_uploaded":
            raise HTTPException(400, "Clip has not been uploaded yet")

        from clipper.upload.youtube import publish_video

        ok = publish_video(
            video_id,
            channel=clip.get("channel"),
            config=config,
        )
        if ok:
            clip["_privacy"] = "public"
            meta_path.write_text(json.dumps(clip, indent=2))
        return {"video_id": video_id, "published": ok, "channel": clip.get("channel")}

    @app.get("/api/releases")
    def get_releases(channel: str = Query("all")):
        from clipper.db import list_releases

        channel_key = str(channel or "").strip()
        if channel_key.lower() in ("", "all", "*"):
            channel_key = None

        releases = list_releases(config, channel=channel_key)
        return {"releases": releases}

    @app.post("/api/releases")
    def create_release(req: ReleaseRequest):
        from clipper.db import create_release as db_create_release

        try:
            scheduled_at = datetime.fromisoformat(req.scheduled_at)
        except ValueError:
            raise HTTPException(400, f"Invalid datetime: {req.scheduled_at}")

        release_id = db_create_release(
            config, req.clip_id, req.channel, scheduled_at.isoformat(),
        )
        return {"id": release_id}

    @app.get("/api/analytics")
    def get_analytics(
        days: int = Query(90),
        refresh: bool = Query(False),
        channel: str = Query("all"),
    ):
        from clipper.analytics import fetch_channel_recent, fetch_channels_recent

        channel_key = str(channel or "all").strip()
        if channel_key and channel_key.lower() not in ("all", "*"):
            return {"videos": fetch_channel_recent(days=days, refresh=refresh, channel=channel_key, config=config)}
        videos, errors = fetch_channels_recent(config, days=days, refresh=refresh)
        return {"videos": videos, "errors": errors}

    @app.get("/api/analytics/{video_id}/retention")
    def get_retention_curve(video_id: str):
        """Return audience retention curve for a video."""
        from clipper.analytics import fetch_retention_curve
        curve = fetch_retention_curve(video_id)
        if curve is None:
            return {"video_id": video_id, "curve": None, "error": "Retention data unavailable"}
        return {"video_id": video_id, "curve": curve}

    @app.get("/api/growth/scoreboard")
    def get_growth_scoreboard(
        days: int = Query(90),
        refresh: bool = Query(False),
        channel: str = Query("all"),
    ):
        from clipper.analytics import build_growth_scoreboard, build_kill_scale_recommendations

        try:
            payload = build_growth_scoreboard(config, days=days, refresh=refresh, channel=channel)
            payload["kill_scale"] = build_kill_scale_recommendations(
                config,
                hours=2,
                baseline_days=days,
                refresh=refresh,
                board=payload,
                channel=channel,
            )
            return payload
        except Exception as e:
            raise HTTPException(500, f"Growth scoreboard failed: {e}")

    # -- Discover endpoints --

    @app.get("/api/discover/trending")
    def get_trending():
        """Fetch top 20 trending games from Twitch with clip stats."""
        from clipper.discover.trends import _get_twitch_auth, fetch_twitch_trending

        try:
            _, headers = _get_twitch_auth()
            games = fetch_twitch_trending(headers)
            return {"games": games, "degraded": False, "source": "twitch"}
        except Exception as e:
            logger.warning("Trending fallback to local data: %s", e)
            fallback_games = _build_local_trending_fallback(config)
            return {
                "games": fallback_games,
                "degraded": True,
                "source": "local",
                "detail": f"Twitch API unavailable: {e}",
            }

    @app.get("/api/discover/gaps")
    def get_gaps(limit: int = Query(20, ge=5, le=50)):
        from clipper.discover.gaps import analyze_gaps
        return analyze_gaps(config, limit=limit)

    # -- Autopilot endpoints --

    @app.post("/api/autopilot/start")
    def autopilot_start(req: AutopilotStartRequest):
        if workflow_thread["current"] and workflow_thread["current"].is_alive():
            raise HTTPException(409, "A workflow is already running")

        channel_key = (req.channel or "").strip() or None
        channels_cfg = config.get("channels", {}) or {}
        if channel_key and channel_key not in channels_cfg:
            raise HTTPException(400, f"Unknown channel: {channel_key}")

        state.reset(recipe="autopilot", phase="learning", detail="Refreshing game stats...")

        def _run():
            import copy
            from clipper.learn import collect_game_stats, collect_performance, train_weights
            from clipper.fetch.twitch import fetch_twitch_clips
            from clipper.workflow import _load_and_score_pending, _approve_clips, _process_clips

            try:
                # Step 1: Refresh game stats
                try:
                    game_stats = collect_game_stats(config)
                except Exception:
                    game_stats = {"games": {}}

                # Step 2: Train weights
                state.set_phase("learning", "Training scoring weights...")
                try:
                    collect_performance(config)
                    train_weights(config)
                except Exception:
                    pass  # continue with defaults

                # Step 3: Fetch trending clips
                state.set_phase("fetching", "Discovering trending clips...")
                fetch_config = copy.deepcopy(config)
                fetch_config["targets"]["twitch"]["clips_per_source"] = 100

                game_priorities = {}
                for game, stats in game_stats.get("games", {}).items():
                    game_priorities[game] = stats.get("multiplier", 1.0)

                fetch_twitch_clips(
                    fetch_config, discover_mode=True,
                    game_priorities=game_priorities,
                )

                # Step 4: Score with game multipliers
                state.set_phase("scoring", "Scoring with game multipliers...")
                pending = _load_and_score_pending(config, use_game_multipliers=True)

                if not pending:
                    state.set_phase("done", "No qualifying clips found")
                    return

                qualifying = [c for c in pending if c["_score"] >= req.min_score]
                if not qualifying:
                    state.set_phase("done", f"No clips above score {req.min_score}")
                    return

                # Step 5: Approve & process
                to_approve = qualifying[:req.count]
                approved = _approve_clips(to_approve, req.count, config, channel=channel_key)

                if approved == 0:
                    state.set_phase("done", "No clips approved")
                    return

                state.set_phase("processing", f"Processing {approved} clips...")
                _process_clips(config, state=state)

                if state.phase != "error":
                    state.set_phase("done", f"{state.completed} clips processed")
            except Exception as e:
                state.set_error(str(e))

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        workflow_thread["current"] = t

        return {"status": "started"}

    # -- Compilation endpoints --

    @app.get("/api/compilations")
    def get_compilations(channel: str = Query("all")):
        """Return built compilations from output/ (ready to upload/publish)."""
        from clipper.db import get_db

        channel_key = str(channel or "").strip()
        if channel_key.lower() in ("", "all", "*"):
            channel_key = ""

        conn = get_db(config)
        conditions = ["status = 'output'", "id LIKE 'compilation_%'"]
        params: list = []
        if channel_key:
            conditions.append("channel = ?")
            params.append(channel_key)

        where = f"WHERE {' AND '.join(conditions)}"
        rows = conn.execute(f"SELECT * FROM clips {where} ORDER BY updated_at DESC", params).fetchall()

        from clipper.db import _row_to_clip
        comps = []
        for r in rows:
            data = _row_to_clip(r)
            if not data.get("processed_path") or not Path(data["processed_path"]).exists():
                continue
            comps.append(data)

        return {"compilations": comps}

    @app.get("/api/compilation/clips")
    def get_compilation_clips(channel: str = Query("all")):
        """Return output clips eligible for compilation (have processed_path, not compilations)."""
        from clipper.process.score import score_clip
        from clipper.learn import get_learned_weights
        from clipper.db import list_clips

        channel_key = str(channel or "").strip()
        if channel_key.lower() in ("", "all", "*"):
            channel_key = ""

        weights = get_learned_weights(config)
        raw = list_clips(
            config,
            status="output",
            channel=channel_key if channel_key else None,
            sort="score",
            limit=500,
            exclude_compilations=True,
        )

        clips = []
        for data in raw:
            if not data.get("processed_path") or not Path(data["processed_path"]).exists():
                continue
            if data.get("is_shorts"):
                continue
            if "_score" not in data:
                data["_score"] = round(score_clip(data, weights=weights), 1)
            clips.append(data)

        clips.sort(key=lambda c: c.get("_score", 0), reverse=True)
        return {"clips": clips}

    @app.post("/api/compilation/build")
    def build_compilation(req: CompilationBuildRequest):
        if workflow_thread["current"] and workflow_thread["current"].is_alive():
            raise HTTPException(409, "A workflow is already running")

        if len(req.clip_ids) < 2:
            raise HTTPException(400, "Need at least 2 clips")

        channel_key = (req.channel or "").strip() or None
        channels_cfg = config.get("channels", {}) or {}
        if channel_key and channel_key not in channels_cfg:
            raise HTTPException(400, f"Unknown channel: {channel_key}")

        # Resolve clip metadata from IDs
        output_dir = config["_output_dir"]
        ordered_clips = []
        for clip_id in req.clip_ids:
            meta_path = _resolve_clip_meta(output_dir, clip_id)
            ordered_clips.append(json.loads(meta_path.read_text()))

        # Reset state
        state.compile_step = ""
        state.compile_progress = 0.0
        state.started_at = time.time()

        def _run():
            from clipper.process.compile import compile_clips, build_thumbnail, build_description
            from datetime import datetime as dt

            date_str = dt.now().strftime("%Y%m%d")
            game = ordered_clips[0].get("game", "mixed")
            game_slug = game.lower().replace(" ", "_")
            comp_name = f"compilation_{game_slug}_{date_str}.mp4"
            comp_path = output_dir / comp_name
            title = req.title or f"{game.upper()} Highlights — Best Clips {dt.now().strftime('%m/%d')}"

            state.compile_step = "compiling"
            compile_clips(ordered_clips, comp_path, config, countdown=req.countdown)

            state.compile_step = "thumbnail"
            state.compile_progress = 0.8
            thumb_path = comp_path.with_suffix(".jpg")
            build_thumbnail(ordered_clips, thumb_path, title=title, game=game)

            state.compile_step = "description"
            state.compile_progress = 0.9
            description = build_description(ordered_clips, game=game)

            # Save compilation metadata
            comp_id = comp_path.stem
            # Get duration from file
            comp_duration = sum(c.get("duration", 0) for c in ordered_clips)
            streamers = sorted(set(c.get("streamer", "") for c in ordered_clips if c.get("streamer")))
            comp_meta = {
                "id": comp_id,
                "title": title,
                "_title_override": title,
                "_description_override": description,
                "streamer": ", ".join(streamers),
                "game": game,
                "platform": "twitch",
                "url": "",
                "duration": round(comp_duration, 1),
                "view_count": 0,
                "processed_path": str(comp_path),
                "is_shorts": False,
                "clip_count": len(ordered_clips),
            }
            # Stamp the workspace/target channel so the UI can scope output by channel.
            inferred = channel_key or ordered_clips[0].get("_target_channel") or ordered_clips[0].get("channel")
            if inferred:
                comp_meta["_target_channel"] = inferred
            meta_out = comp_path.with_suffix(".json")
            meta_out.write_text(json.dumps(comp_meta, indent=2))

            state.compile_step = ""
            state.compile_progress = 1.0

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        workflow_thread["current"] = t

        return {"status": "building", "clip_count": len(ordered_clips)}

    # -- Learning endpoints --

    @app.post("/api/learn/collect")
    def learn_collect():
        from clipper.learn import collect_performance

        collected = collect_performance(config)
        return {"collected": collected}

    @app.post("/api/learn/train")
    def learn_train():
        from clipper.learn import train_weights

        result = train_weights(config)
        if result is None:
            raise HTTPException(400, "Not enough data to train weights")
        return result

    @app.get("/api/learn/status")
    def learn_status():
        from clipper.db import get_weights

        data = get_weights(config)
        if not data:
            return {"learned": False, "sample_count": 0, "weights": None}

        return {
            "learned": True,
            "sample_count": data.get("sample_size", 0),
            "weights": data.get("weights"),
        }

    # -- Thumbnail endpoints --

    @app.get("/api/clips/{clip_id}/thumbnail")
    def get_thumbnail(
        clip_id: str,
        source: bool = Query(False),
        at: float | None = Query(None, ge=0.0, le=0.95),
    ):
        """Extract and serve thumbnail JPEG.

        - If `at` is provided (0..0.95), extract a frame at that fraction of the video duration.
        - Otherwise, use scene-detection-based extraction for a visually "best" frame.

        Thumbnails are cached next to the video file: {stem}.thumb[.atXX].jpg.
        """
        from clipper.config import get_ffmpeg, get_ffprobe
        from clipper.upload.youtube import _extract_thumbnail

        output_dir = config["_output_dir"]
        clip: dict | None = None
        video_path: str | None = None

        try:
            meta_path = _resolve_clip_meta(output_dir, clip_id)
            clip = json.loads(meta_path.read_text())
            video_path = clip.get("processed_path")
            if source:
                # Try the pre-format source asset if present, otherwise fall back to
                # the downloaded clip_id.mp4 (yt-dlp display_id) if it exists.
                candidate = clip.get("_source_path")
                if candidate and Path(str(candidate)).exists():
                    video_path = str(candidate)
                else:
                    raw = output_dir / f"{clip_id}.mp4"
                    if raw.exists():
                        video_path = str(raw)
        except HTTPException:
            clip = None

        # If the JSON doesn't exist, we may still have a publishable MP4 in output.
        if clip is None:
            try:
                video_path = str(_resolve_output_video_path(output_dir, clip_id))
            except HTTPException:
                video_path = None

        # Fallback: pending/approved clips (no local output meta yet). Use the platform thumbnail_url
        # and cache it locally so the crop UI always has a stable same-origin image.
        if clip is None and not video_path:
            queue_dir = config["_queue_dir"]
            # Check JSON files on disk first (legacy path)
            for sub in ("pending", "approved", "skipped"):
                candidate = queue_dir / sub / f"{clip_id}.json"
                if candidate.exists():
                    try:
                        clip = json.loads(candidate.read_text())
                    except Exception:
                        clip = None
                    break

            # Fall back to SQLite DB if no JSON file found
            if clip is None:
                from clipper.db import get_clip as db_get_clip
                clip = db_get_clip(config, clip_id)

            thumb_url = (clip or {}).get("thumbnail_url")
            if not isinstance(thumb_url, str) or not thumb_url.strip():
                raise HTTPException(404, f"Clip {clip_id} not found")

            # Prefer a larger thumbnail if possible (Twitch uses preview-{WxH}.jpg).
            for size in ("1920x1080", "1280x720", "640x360", "480x272"):
                if "preview-" in thumb_url:
                    thumb_url = re.sub(r"preview-\d+x\d+\.jpg", f"preview-{size}.jpg", thumb_url)
                    break

            cache_dir = queue_dir / "thumb_cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path = cache_dir / f"{clip_id}{'.source' if source else ''}.thumb.jpg"

            min_bytes = 50_000  # avoid getting stuck with tiny thumbnails after URL/template changes
            if not cache_path.exists() or cache_path.stat().st_size < min_bytes:
                try:
                    import requests

                    r = requests.get(thumb_url, timeout=10)
                    if r.status_code != 200:
                        raise HTTPException(502, f"Thumbnail fetch failed: {r.status_code}")
                    cache_path.write_bytes(r.content)
                except HTTPException:
                    raise
                except Exception as e:
                    raise HTTPException(502, f"Thumbnail fetch failed: {e}")

            return FileResponse(str(cache_path), media_type="image/jpeg")

        if not video_path or not Path(video_path).exists():
            raise HTTPException(404, "Video file not found")

        # Cache path: {video_stem}.thumb[.atXX].jpg
        tag = f".at{int(round(at * 100)):02d}" if at is not None else ""
        thumb_path = Path(video_path).with_suffix(f"{'.source.thumb' if source else '.thumb'}{tag}.jpg")
        if not thumb_path.exists():
            if at is None:
                result = _extract_thumbnail(video_path, str(thumb_path))
                if not result:
                    raise HTTPException(500, "Thumbnail extraction failed")
            else:
                try:
                    probe = subprocess.run(
                        [
                            get_ffprobe(),
                            "-v",
                            "quiet",
                            "-show_entries",
                            "format=duration",
                            "-of",
                            "default=noprint_wrappers=1:nokey=1",
                            str(video_path),
                        ],
                        capture_output=True,
                        text=True,
                        timeout=8,
                        check=True,
                    )
                    duration = float(probe.stdout.strip() or "0")
                    timestamp = max(0.0, min(duration * at, max(0.0, duration - 0.05)))
                    subprocess.run(
                        [
                            get_ffmpeg(),
                            "-y",
                            "-ss",
                            str(timestamp),
                            "-i",
                            str(video_path),
                            "-vframes",
                            "1",
                            "-vf",
                            "eq=saturation=1.3:contrast=1.1",
                            "-q:v",
                            "2",
                            str(thumb_path),
                        ],
                        capture_output=True,
                        text=True,
                        timeout=12,
                        check=True,
                    )
                except Exception as e:
                    raise HTTPException(500, f"Thumbnail extraction failed: {e}")

        return FileResponse(str(thumb_path), media_type="image/jpeg")

    @app.post("/api/clips/{clip_id}/thumbnail/regenerate")
    def regenerate_thumbnail(clip_id: str):
        """Delete cached thumbnail and re-extract."""
        from clipper.upload.youtube import _extract_thumbnail

        output_dir = config["_output_dir"]
        meta_path = _resolve_clip_meta(output_dir, clip_id)

        clip = json.loads(meta_path.read_text())
        video_path = clip.get("processed_path")
        if not video_path or not Path(video_path).exists():
            raise HTTPException(404, "Video file not found")

        thumb_path = Path(video_path).with_suffix(".thumb.jpg")
        if thumb_path.exists():
            thumb_path.unlink()

        result = _extract_thumbnail(video_path, str(thumb_path))
        if not result:
            raise HTTPException(500, "Thumbnail extraction failed")

        return {"ok": True}

    # -- Static files / SPA fallback (production) --
    web_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
    if web_dist.exists():
        index_file = web_dist / "index.html"
        web_root = web_dist.resolve()

        @app.get("/", include_in_schema=False)
        def spa_index():
            return FileResponse(str(index_file))

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa_files(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(404, "Not Found")

            candidate = (web_dist / full_path).resolve()
            if str(candidate).startswith(str(web_root)) and candidate.is_file():
                return FileResponse(str(candidate))
            return FileResponse(str(index_file))

    return app


if __name__ == "__main__":
    import uvicorn

    cfg = load_config()
    app = create_app(cfg)
    uvicorn.run(app, host="localhost", port=8420)
