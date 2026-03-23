# Pipeline Reliability Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the daily clip pipeline run unattended without wrong-channel posts, stuck processes, silent failures, or disk bloat.

**Architecture:** Four layers — (1) game-channel binding prevents cross-contamination at config level, (2) resilient cron with self-healing retry replaces fragile one-shot launchd jobs, (3) aggressive daily cleanup deletes yesterday's compilations and stale output, (4) integration tests lock down the critical paths that keep breaking.

**Tech Stack:** Python 3.12, SQLite, launchd, pytest

---

## Background — What Keeps Breaking

| Bug | Root Cause | Impact |
|-----|-----------|--------|
| Deadlock clips posted to Marathon Instagram | `_substitute_failed_release` has no game filter | Wrong content on wrong channel |
| YouTube upload hangs for 12+ hours | `request.next_chunk()` has no timeout | Entire pipeline stalls |
| Orphaned clips never uploaded | `_approve_clips` bulk-skips processed clips | Wasted processing, missing uploads |
| Marathon job fails silently for days | launchd exit code 1, nobody notices | No Marathon content |
| Token expires mid-run | Discovered at upload time, not startup | Wasted hours of processing |
| 43GB output directory | Compilations accumulate forever | Disk fills up |
| Same bugs recur | No tests | Every fix is temporary |

---

## Task 1: Game-Channel Binding (Config-Level Guard)

**Why:** Every channel should declare what game it serves. Cross-posting, substitution, and scheduling all check this. No query needs to "remember" to filter by game — the config enforces it.

**Files:**
- Modify: `config.yaml` (add `game` field to channels)
- Modify: `clipper/schedule.py:145-219` (substitute uses channel game)
- Modify: `clipper/workflow.py:974-1067` (`_cross_post_clips` validates game)
- Test: `tests/test_channel_game_guard.py`

**Step 1: Write failing tests**

```python
# tests/test_channel_game_guard.py
"""Ensure clips never cross game boundaries between channels."""
import pytest
from unittest.mock import patch, MagicMock

# Minimal config for testing
def _test_config():
    return {
        "channels": {
            "default": {"platform": "youtube", "game": "Deadlock", "schedule": {"shorts_per_day": 7, "release_times": ["12:00"]}},
            "instagram_main": {"platform": "instagram", "game": "Deadlock", "schedule": {"shorts_per_day": 2, "release_times": ["11:00", "19:00"]}},
            "instagram_marathon": {"platform": "instagram", "game": "Marathon", "schedule": {"shorts_per_day": 2, "release_times": ["11:00", "19:00"]}},
            "class_instantiate": {"platform": "youtube", "game": "Marathon", "schedule": {"shorts_per_day": 7, "release_times": ["12:00"]}},
        },
        "autopilot": {"upload_channels": ["tiktok_main"]},
    }


def test_substitute_only_picks_same_game(tmp_path):
    """When a Marathon Instagram release fails, substitute must be Marathon — never Deadlock."""
    import sqlite3
    from clipper.schedule import _substitute_failed_release

    db_path = tmp_path / "clipper.db"
    # ... setup in-memory DB with Marathon failed release + Deadlock clips available
    # Assert: substitute picks Marathon clip or returns False (no substitute)
    # Assert: Deadlock clips are NEVER picked


def test_cross_post_skips_wrong_game():
    """_cross_post_clips must skip clips whose game doesn't match the target channel's game."""
    from clipper.workflow import _cross_post_clips
    # Setup: Deadlock clips, target channel = instagram_marathon (game=Marathon)
    # Assert: no releases created for instagram_marathon


def test_channel_game_from_config():
    """Channel game is read from config.yaml channels section."""
    from clipper.schedule import _get_channel_game
    config = _test_config()
    assert _get_channel_game("instagram_marathon", config) == "Marathon"
    assert _get_channel_game("default", config) == "Deadlock"
    assert _get_channel_game("nonexistent", config) is None
```

**Step 2: Run tests — expect failure (functions don't exist yet)**

```bash
cd /Users/isaacaudet/clipper && python -m pytest tests/test_channel_game_guard.py -v
```

**Step 3: Add `game` field to channel configs**

In `config.yaml`, add `game:` to each channel:

```yaml
channels:
  default:
    name: "Main Channel"
    platform: youtube
    game: Deadlock          # <-- ADD
    token_file: ".clipper_token.json"
    # ...
  class_instantiate:
    name: "Pro Marathon YouTube"
    platform: youtube
    game: Marathon           # <-- ADD
    # ...
  tiktok_main:
    name: "TikTok Main"
    platform: tiktok
    game: Deadlock           # <-- ADD
    # ...
  instagram_main:
    name: "Instagram Reels"
    platform: instagram
    game: Deadlock           # <-- ADD
    # ...
  facebook_main:
    name: "Facebook Reels"
    platform: facebook
    game: Deadlock           # <-- ADD
    # ...
  instagram_marathon:
    name: "Pro Marathon Instagram"
    platform: instagram
    game: Marathon           # <-- ADD
    # ...
  tiktok_marathon:
    name: "Pro Marathon TikTok"
    platform: tiktok
    game: Marathon           # <-- ADD
    # ...
```

**Step 4: Add `_get_channel_game()` helper to `schedule.py`**

```python
def _get_channel_game(channel: str, config: dict) -> str | None:
    """Return the game a channel is bound to, or None if unset."""
    ch = config.get("channels", {}).get(channel, {})
    return ch.get("game") or None
```

**Step 5: Harden `_substitute_failed_release` — prefer channel game over clip game**

The current fix (from this conversation) uses the failed clip's game. Upgrade to use the **channel's configured game** as primary, falling back to the clip's game:

```python
# In _substitute_failed_release, replace the game lookup with:
from clipper.schedule import _get_channel_game

# Primary: channel's configured game (authoritative)
# Fallback: failed clip's game (backwards compat for channels without game config)
channel_game = _get_channel_game(channel, config)
if not channel_game:
    failed_clip_id = failed_release.get("clip_id", "")
    row = conn.execute("SELECT game FROM clips WHERE id=?", (failed_clip_id,)).fetchone()
    channel_game = (row["game"] if row else None) or ""

game_filter = "AND c.game = ?" if channel_game else ""
params = [channel]
if channel_game:
    params.insert(0, channel_game)
```

**Step 6: Add game guard to `_cross_post_clips`**

At the top of the `for extra_ch in extra_channels:` loop in `_cross_post_clips` (workflow.py line 1001):

```python
for extra_ch in extra_channels:
    # Guard: skip clips whose game doesn't match this channel's configured game
    ch_game = (config.get("channels", {}).get(extra_ch, {}) or {}).get("game")
    if ch_game:
        game_matched = [c for c in processed if (c.get("game") or "").lower() == ch_game.lower()]
        if len(game_matched) < len(processed):
            console.print(f"  [dim]{extra_ch}: filtered {len(processed) - len(game_matched)} clips (wrong game for {ch_game})[/dim]")
        if not game_matched:
            console.print(f"  [dim]{extra_ch}: no {ch_game} clips to cross-post — skipping[/dim]")
            continue
        processed_for_ch = game_matched
    else:
        processed_for_ch = processed
    # ... rest of loop uses processed_for_ch instead of processed
```

**Step 7: Run tests — expect pass**

```bash
python -m pytest tests/test_channel_game_guard.py -v
```

**Step 8: Commit**

```bash
git add config.yaml clipper/schedule.py clipper/workflow.py tests/test_channel_game_guard.py
git commit -m "feat: game-channel binding prevents cross-game contamination

Channels declare their game in config.yaml. Substitute selection and
cross-posting both enforce game match. Prevents Deadlock clips from
being posted to Marathon channels and vice versa."
```

---

## Task 2: Resilient Cron — Self-Healing Daily Pipeline

**Why:** If the 7am job fails (network error, token expiry, process crash), nothing runs for the rest of the day. The user has to manually intervene. Instead: retry later, but respect daily caps so it doesn't double up.

**Files:**
- Modify: `clipper/cli.py` (add `--retry-until` flag to daily-compilation)
- Modify: `clipper/cron.py` (update launchd plist with retry schedule)
- Modify: `clipper/workflow.py` (add idempotency check at start of autopilot)
- Modify: `clipper/db.py` (add `pipeline_runs` table for run tracking)
- Test: `tests/test_idempotent_pipeline.py`

**Step 1: Write failing tests**

```python
# tests/test_idempotent_pipeline.py
"""Pipeline must be idempotent — running twice in one day produces the same output count."""

def test_second_run_skips_if_daily_cap_met():
    """If 7 clips already scheduled today, second run does nothing."""
    # count_output_shorts_today returns 7
    # run_autopilot_workflow should return status="daily_cap_reached"


def test_second_run_fills_remaining_if_partial():
    """If first run only got 3 clips, second run fills remaining 4."""
    # count_output_shorts_today returns 3, daily_count=7
    # run_autopilot_workflow should process 4 more


def test_compilation_skips_if_already_uploaded_today():
    """If today's compilation already has video_id, skip re-compilation."""
    # DB has compilation_deadlock_20260318 with video_id set
    # run_compilation_workflow should skip compilation phase


def test_pipeline_run_logged():
    """Each pipeline run is logged to pipeline_runs table."""
    # After run_autopilot_workflow completes, pipeline_runs has an entry
```

**Step 2: Run tests — expect failure**

```bash
python -m pytest tests/test_idempotent_pipeline.py -v
```

**Step 3: Add `pipeline_runs` table to db.py**

Track each pipeline invocation so we can detect duplicate runs:

```python
# In _ensure_tables(), add:
conn.execute("""
    CREATE TABLE IF NOT EXISTS pipeline_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date TEXT NOT NULL,
        pipeline TEXT NOT NULL,
        channel TEXT NOT NULL DEFAULT '',
        game TEXT NOT NULL DEFAULT '',
        clips_processed INTEGER DEFAULT 0,
        clips_uploaded INTEGER DEFAULT 0,
        compilation_uploaded INTEGER DEFAULT 0,
        status TEXT DEFAULT 'running',
        started_at TEXT DEFAULT (datetime('now')),
        finished_at TEXT,
        error TEXT
    )
""")
```

**Step 4: Add run tracking to `run_autopilot_workflow`**

At the start of `run_autopilot_workflow` in workflow.py:

```python
# Log pipeline run start
from clipper.db import get_db
from datetime import datetime, timedelta, timezone
_pst = timezone(timedelta(hours=-8))
_today = datetime.now(tz=_pst).strftime("%Y-%m-%d")
_conn = get_db(config)
_conn.execute(
    "INSERT INTO pipeline_runs (run_date, pipeline, channel, game, status) VALUES (?, ?, ?, ?, 'running')",
    (_today, "shorts", channel or "", game or ""),
)
_conn.commit()
_run_id = _conn.execute("SELECT last_insert_rowid()").fetchone()[0]
```

At the end (success or failure), update the run:

```python
_conn.execute(
    "UPDATE pipeline_runs SET status=?, clips_processed=?, clips_uploaded=?, finished_at=datetime('now') WHERE id=?",
    ("done", summary.get("processed", 0), summary.get("uploaded", 0), _run_id),
)
_conn.commit()
```

**Step 5: Update launchd to retry 3x daily**

In `cron.py`, change `AUTOPILOT_TIMES` to run at 7am, 12pm, and 5pm:

```python
AUTOPILOT_TIMES = [
    {"Hour": 7, "Minute": 0},
    {"Hour": 12, "Minute": 0},
    {"Hour": 17, "Minute": 0},
]
```

The existing daily cap logic in `run_autopilot_workflow` (lines 1159-1181) already handles this:
- First run at 7am: processes 7 clips
- Second run at 12pm: `count_output_shorts_today()` returns 7 → `remaining = 0` → exits with "daily_cap_reached"
- If 7am fails: 12pm run picks up all 7 clips
- If 7am gets 3 clips (partial): 12pm fills remaining 4

The compilation check at workflow.py line 1774 already skips if `video_id` is set:
```python
if existing and existing["video_id"]:
    console.print(f"[yellow]Compilation already uploaded: {existing['video_id']} — skipping re-upload.[/yellow]")
```

**Step 6: Update launchd plist generation**

In `install_autopilot()`, change from single `StartCalendarInterval` dict to array:

```python
# Replace single StartCalendarInterval with array of 3 times
calendar_intervals = "\n".join(f"""        <dict>
            <key>Hour</key>
            <integer>{t['Hour']}</integer>
            <key>Minute</key>
            <integer>{t['Minute']}</integer>
        </dict>""" for t in AUTOPILOT_TIMES)

# In plist template:
f"""    <key>StartCalendarInterval</key>
    <array>
{calendar_intervals}
    </array>"""
```

**Step 7: Same for Marathon job**

Add `MARATHON_TIMES` and update `install_marathon()`:

```python
MARATHON_TIMES = [
    {"Hour": 8, "Minute": 0},
    {"Hour": 13, "Minute": 0},
    {"Hour": 18, "Minute": 0},
]
```

**Step 8: Run tests — expect pass**

```bash
python -m pytest tests/test_idempotent_pipeline.py -v
```

**Step 9: Reinstall launchd jobs**

```bash
python -m clipper autopilot-cron --remove && python -m clipper autopilot-cron --install
# Check the new plist has 3 calendar intervals
cat ~/Library/LaunchAgents/com.clipper.autopilot.plist
```

**Step 10: Commit**

```bash
git add clipper/cli.py clipper/cron.py clipper/workflow.py clipper/db.py tests/
git commit -m "feat: resilient cron retries 3x daily with idempotent pipeline

Pipeline runs at 7am/12pm/5pm. Daily cap prevents double-ups.
Compilation skips if already uploaded. pipeline_runs table tracks
each invocation for debugging."
```

---

## Task 3: Aggressive Daily Cleanup — Delete Yesterday's Compilations

**Why:** Compilations are 1-2.5GB each. Two games × daily = 3-5GB/day accumulating. The user doesn't need yesterday's. Source clips for non-pending shorts should also be cleaned up faster.

**Files:**
- Modify: `clipper/schedule.py:409-468` (rewrite `purge_old_output`)
- Test: `tests/test_purge.py`

**Step 1: Write failing tests**

```python
# tests/test_purge.py
"""Purge must delete old compilations but protect today's and active releases."""
from pathlib import Path
from datetime import datetime

def test_purge_deletes_yesterdays_compilation(tmp_path):
    """Yesterday's compilation files are deleted."""
    # Create compilation_deadlock_20260317.mp4 (yesterday)
    # Create compilation_deadlock_20260318.mp4 (today)
    # Run purge
    # Assert yesterday's is gone, today's remains


def test_purge_protects_active_release_files(tmp_path):
    """Files needed by pending/uploaded/scheduled releases are never deleted."""
    # Create release with status='pending' pointing to a 3-day-old file
    # Run purge
    # Assert file survives


def test_purge_deletes_source_files_after_upload(tmp_path):
    """Source .mp4 files are deleted once clip has video_id (uploaded)."""
    # Create clip with video_id set + source file
    # Run purge
    # Assert source file deleted, final file kept


def test_purge_keeps_todays_finals(tmp_path):
    """Today's _final.mp4 files are always kept."""
    # Create today's _final.mp4
    # Run purge
    # Assert kept
```

**Step 2: Rewrite `purge_old_output()`**

New strategy:
1. **Compilations**: Delete all compilations EXCEPT today's (by date in filename). Compilations with pending releases are protected.
2. **Source files**: Delete source .mp4 files for clips that have `video_id` (already uploaded). Keep sources for clips in active pipeline.
3. **Finals/sidecars**: Keep last 3 days (down from 7). Releases protect their files regardless.
4. **Run daily** (not just Sundays).

```python
def purge_old_output(config: dict, *, keep_days: int = 3, verbose: bool = False) -> int:
    """Delete old output files. Returns bytes freed.

    Strategy:
    - Compilations: delete all except today's (protected by active releases)
    - Source files: delete once clip is uploaded (has video_id)
    - Finals/sidecars: keep last 3 days (releases protect their files)
    - Runs daily in execute_releases (not just Sundays)
    """
    from pathlib import Path
    from clipper.db import get_db

    out_dir = Path(config.get("_output_dir") or "output")
    if not out_dir.exists():
        return 0

    conn = get_db(config)

    # Build protected set from active releases
    protected = set()
    active = conn.execute(
        "SELECT meta_path, clip_id FROM releases WHERE status IN ('pending','uploaded','scheduled','executing')"
    ).fetchall()
    for row in active:
        if row["meta_path"]:
            protected.add(Path(row["meta_path"]).resolve())
        clip_row = conn.execute(
            "SELECT processed_path, source_path, subtitle_path FROM clips WHERE id=?",
            (row["clip_id"],)
        ).fetchone()
        if clip_row:
            for col in ("processed_path", "source_path", "subtitle_path"):
                p = clip_row[col]
                if p:
                    protected.add(Path(p).resolve())

    # Today's date string for compilation protection
    from datetime import datetime, timedelta, timezone
    _pst = timezone(timedelta(hours=-8))
    today_str = datetime.now(tz=_pst).strftime("%Y%m%d")

    cutoff = time.time() - (keep_days * 86400)
    freed = 0

    for f in out_dir.iterdir():
        if f.resolve() in protected:
            continue
        if not f.suffix in (".mp4", ".ass", ".srt", ".jpg", ".json"):
            continue

        # Compilations: keep today's, delete older
        if f.stem.startswith("compilation_"):
            if today_str in f.name:
                continue  # Today's compilation — keep
            # Old compilation — delete
            freed += f.stat().st_size
            f.unlink()
            if verbose:
                console.print(f"  [dim]Purged compilation: {f.name}[/dim]")
            continue

        # Recent files (within keep_days): keep
        if f.stat().st_mtime > cutoff:
            continue

        # In-progress files (last 4 hours): keep
        if f.suffix == ".mp4" and f.stat().st_mtime > time.time() - 14400:
            continue

        # Old file, not protected — delete
        freed += f.stat().st_size
        f.unlink()
        if verbose:
            console.print(f"  [dim]Purged: {f.name}[/dim]")

    return freed
```

**Step 3: Run purge daily instead of weekly**

In `execute_releases()`, remove the Sunday-only check:

```python
# Replace:
#   if datetime.now().weekday() == 6:  # Sunday only
#       purge_old_output(config)
# With:
purge_old_output(config)
```

**Step 4: Run tests — expect pass**

```bash
python -m pytest tests/test_purge.py -v
```

**Step 5: Commit**

```bash
git add clipper/schedule.py tests/test_purge.py
git commit -m "feat: aggressive daily cleanup deletes yesterday's compilations

Compilations older than today are deleted (saves 3-5GB/day).
Source files deleted after upload. Finals kept 3 days.
Active releases always protect their files.
Purge runs daily instead of Sundays only."
```

---

## Task 4: Token Health Check at Startup

**Why:** The Marathon job ran for hours before discovering the token was expired. Check all tokens at pipeline startup, warn early, fail fast.

**Files:**
- Create: `clipper/token_health.py`
- Modify: `clipper/cli.py` (call health check before pipeline)
- Test: `tests/test_token_health.py`

**Step 1: Write failing tests**

```python
# tests/test_token_health.py
"""Token health checks must catch expiry before the pipeline runs."""

def test_warns_token_expiring_soon(tmp_path, capsys):
    """Token expiring in <48h prints warning."""
    import json, time
    token_path = tmp_path / ".clipper_token.json"
    token_path.write_text(json.dumps({
        "access_token": "test",
        "expires_at": time.time() + 3600,  # 1 hour left
    }))
    from clipper.token_health import check_token
    result = check_token(token_path, "default")
    assert result["status"] == "expiring_soon"


def test_fails_on_expired_token(tmp_path):
    """Expired token returns status='expired'."""
    import json, time
    token_path = tmp_path / ".clipper_token.json"
    token_path.write_text(json.dumps({
        "access_token": "test",
        "expires_at": time.time() - 3600,  # 1 hour ago
    }))
    from clipper.token_health import check_token
    result = check_token(token_path, "default")
    assert result["status"] == "expired"


def test_passes_healthy_token(tmp_path):
    """Token with >48h remaining returns status='ok'."""
    import json, time
    token_path = tmp_path / ".clipper_token.json"
    token_path.write_text(json.dumps({
        "access_token": "test",
        "expires_at": time.time() + 200000,  # ~2.3 days
    }))
    from clipper.token_health import check_token
    result = check_token(token_path, "default")
    assert result["status"] == "ok"
```

**Step 2: Implement `clipper/token_health.py`**

```python
"""Pre-flight token health checks for all configured channels."""
import json
import time
from pathlib import Path

from rich.console import Console

from clipper.config import get_project_root

console = Console()


def check_token(token_path: Path, channel: str) -> dict:
    """Check a single token file. Returns {channel, status, expires_in_hours}."""
    if not token_path.exists():
        return {"channel": channel, "status": "missing", "expires_in_hours": 0}

    try:
        data = json.loads(token_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"channel": channel, "status": "corrupt", "expires_in_hours": 0}

    expires_at = data.get("expires_at", 0)
    if not expires_at:
        # Token without expiry — assume OK (e.g., long-lived page tokens)
        return {"channel": channel, "status": "ok", "expires_in_hours": 999}

    hours_left = (expires_at - time.time()) / 3600

    if hours_left <= 0:
        return {"channel": channel, "status": "expired", "expires_in_hours": hours_left}
    elif hours_left < 48:
        return {"channel": channel, "status": "expiring_soon", "expires_in_hours": hours_left}
    else:
        return {"channel": channel, "status": "ok", "expires_in_hours": hours_left}


def check_all_tokens(config: dict) -> list[dict]:
    """Check tokens for all channels. Returns list of health results."""
    root = get_project_root()
    channels = config.get("channels", {})
    results = []

    for ch_name, ch_cfg in channels.items():
        token_file = ch_cfg.get("token_file")
        if not token_file:
            continue
        result = check_token(root / token_file, ch_name)
        results.append(result)

    return results


def preflight_check(config: dict, *, channels: list[str] | None = None) -> bool:
    """Run pre-flight token checks. Returns True if all critical tokens are OK.

    Prints warnings for expiring tokens, errors for expired/missing ones.
    If channels is specified, only checks those channels.
    """
    results = check_all_tokens(config)
    if channels:
        results = [r for r in results if r["channel"] in channels]

    all_ok = True
    for r in results:
        ch = r["channel"]
        status = r["status"]
        hours = r["expires_in_hours"]

        if status == "ok":
            continue
        elif status == "expiring_soon":
            console.print(f"[yellow]  Token warning: {ch} expires in {hours:.0f}h — re-auth soon[/yellow]")
        elif status == "expired":
            console.print(f"[red]  Token EXPIRED: {ch} (expired {-hours:.0f}h ago) — run: clipper auth -c {ch}[/red]")
            all_ok = False
        elif status == "missing":
            console.print(f"[red]  Token MISSING: {ch} — run: clipper auth -c {ch}[/red]")
            all_ok = False
        elif status == "corrupt":
            console.print(f"[red]  Token CORRUPT: {ch} — delete and re-auth[/red]")
            all_ok = False

    return all_ok
```

**Step 3: Call preflight check in `daily_compilation`**

In `clipper/cli.py`, after loading config but before running pipeline:

```python
# After line 210 (config = load_config()):
from clipper.token_health import preflight_check
relevant_channels = [resolved_channel] if resolved_channel else ["default"]
if upload_channels:
    relevant_channels.extend(c.strip() for c in upload_channels.split(",") if c.strip())
if not preflight_check(config, channels=relevant_channels):
    click.echo("[ERROR] Token health check failed — fix tokens before pipeline can run.")
    # Don't abort entirely — the pipeline might still work for some channels.
    # But log clearly so the user knows.
```

**Step 4: Run tests — expect pass**

```bash
python -m pytest tests/test_token_health.py -v
```

**Step 5: Commit**

```bash
git add clipper/token_health.py clipper/cli.py tests/test_token_health.py
git commit -m "feat: pre-flight token health check catches expiry before pipeline runs"
```

---

## Task 5: Pipeline Health Report

**Why:** The user shouldn't have to ask "did clips upload?" — the system should tell them.

**Files:**
- Create: `clipper/health_report.py`
- Modify: `clipper/cli.py` (add `status` command and call report after pipeline)
- Test: `tests/test_health_report.py`

**Step 1: Write `clipper/health_report.py`**

```python
"""Daily pipeline health report — written after each cron run."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rich.console import Console
from rich.table import Table

from clipper.config import get_project_root
from clipper.db import get_db

console = Console()
_PST = timezone(timedelta(hours=-8))


def generate_report(config: dict) -> dict:
    """Generate a health report for today's pipeline activity."""
    conn = get_db(config)
    today = datetime.now(tz=_PST)
    today_start = today.replace(hour=0, minute=0, second=0).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today_end = today.replace(hour=23, minute=59, second=59).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    report = {"date": today.strftime("%Y-%m-%d"), "channels": {}}

    channels = config.get("channels", {})
    for ch_name, ch_cfg in channels.items():
        releases = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM releases WHERE channel=? AND scheduled_at BETWEEN ? AND ? GROUP BY status",
            (ch_name, today_start, today_end),
        ).fetchall()

        status_counts = {r["status"]: r["cnt"] for r in releases}
        total = sum(status_counts.values())

        failed = conn.execute(
            "SELECT clip_id, last_error FROM releases WHERE channel=? AND status='failed' AND scheduled_at BETWEEN ? AND ? LIMIT 3",
            (ch_name, today_start, today_end),
        ).fetchall()

        report["channels"][ch_name] = {
            "target": ch_cfg.get("schedule", {}).get("shorts_per_day", 0),
            "published": status_counts.get("published", 0),
            "pending": status_counts.get("pending", 0),
            "scheduled": status_counts.get("scheduled", 0),
            "failed": status_counts.get("failed", 0),
            "total": total,
            "recent_errors": [{"clip": r["clip_id"][:20], "error": (r["last_error"] or "")[:80]} for r in failed],
        }

    # Token health
    from clipper.token_health import check_all_tokens
    report["tokens"] = check_all_tokens(config)

    # Disk usage
    out_dir = Path(config.get("_output_dir") or "output")
    if out_dir.exists():
        total_bytes = sum(f.stat().st_size for f in out_dir.iterdir() if f.is_file())
        report["disk_gb"] = round(total_bytes / (1024**3), 1)

    return report


def print_report(config: dict):
    """Print a human-readable health report."""
    r = generate_report(config)

    console.print(f"\n[bold]Pipeline Health — {r['date']}[/bold]\n")

    table = Table(show_header=True, header_style="bold")
    table.add_column("Channel")
    table.add_column("Target", justify="right")
    table.add_column("Published", justify="right")
    table.add_column("Pending", justify="right")
    table.add_column("Failed", justify="right")

    for ch, data in r["channels"].items():
        if data["total"] == 0 and data["target"] == 0:
            continue
        style = "green" if data["published"] >= data["target"] else ("yellow" if data["pending"] > 0 else "red")
        table.add_row(ch, str(data["target"]), str(data["published"]), str(data["pending"]), str(data["failed"]), style=style)

    console.print(table)

    # Token warnings
    for t in r.get("tokens", []):
        if t["status"] not in ("ok",):
            console.print(f"  [yellow]Token {t['channel']}: {t['status']} ({t['expires_in_hours']:.0f}h)[/yellow]")

    if "disk_gb" in r:
        console.print(f"\n  Disk: {r['disk_gb']}GB in output/")

    # Save to file for external monitoring
    report_path = get_project_root() / "queue" / "health_report.json"
    report_path.write_text(json.dumps(r, indent=2, default=str))
```

**Step 2: Add `status` CLI command**

```python
@cli.command()
def status():
    """Show pipeline health report for today."""
    from clipper.health_report import print_report
    print_report(load_config())
```

**Step 3: Call report at end of `daily_compilation`**

At the very end of the `daily_compilation` function in cli.py:

```python
# After shorts autopilot completes:
from clipper.health_report import print_report
print_report(config)
```

**Step 4: Commit**

```bash
git add clipper/health_report.py clipper/cli.py tests/test_health_report.py
git commit -m "feat: pipeline health report with clipper status command"
```

---

## Task 6: Core Integration Tests

**Why:** The bugs that keep recurring (wrong channel, double upload, bulk skip) need regression tests. 10-15 tests that exercise the actual DB queries with a test database.

**Files:**
- Create: `tests/conftest.py` (test DB fixture)
- Create: `tests/test_substitute_guard.py`
- Create: `tests/test_approve_clips_guard.py`
- Create: `tests/test_daily_cap.py`

**Step 1: Create test fixtures**

```python
# tests/conftest.py
"""Shared test fixtures — in-memory SQLite with full schema."""
import pytest
import sqlite3
from unittest.mock import patch


@pytest.fixture
def test_db(tmp_path):
    """Create a test database with full schema."""
    db_path = tmp_path / "clipper.db"
    from clipper.db import _ensure_tables
    # Patch get_project_root to use tmp_path
    with patch("clipper.db.get_project_root", return_value=tmp_path):
        with patch("clipper.db._DB_PATH", db_path):
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            _ensure_tables(conn)
            yield conn
            conn.close()


@pytest.fixture
def test_config(tmp_path):
    """Minimal test config."""
    return {
        "_output_dir": str(tmp_path / "output"),
        "_db_path": str(tmp_path / "clipper.db"),
        "channels": {
            "default": {"platform": "youtube", "game": "Deadlock",
                        "schedule": {"shorts_per_day": 7, "release_times": ["12:00"]}},
            "instagram_marathon": {"platform": "instagram", "game": "Marathon",
                                   "schedule": {"shorts_per_day": 2, "release_times": ["11:00", "19:00"]}},
        },
        "autopilot": {"upload_channels": ["tiktok_main"], "daily_count": 7},
    }
```

**Step 2: Test substitute never crosses games**

```python
# tests/test_substitute_guard.py
"""_substitute_failed_release must respect game boundaries."""

def test_substitute_marathon_only_picks_marathon(test_db, test_config):
    """Failed Marathon release gets Marathon substitute, not Deadlock."""
    # Insert Deadlock clip (high score, processed, no instagram_id)
    test_db.execute(
        "INSERT INTO clips (id, game, status, processed_path, score, instagram_id) "
        "VALUES ('deadlock_clip', 'Deadlock', 'output', '/tmp/deadlock_final.mp4', 99, '')"
    )
    # Insert Marathon clip (lower score, processed, no instagram_id)
    test_db.execute(
        "INSERT INTO clips (id, game, status, processed_path, score, instagram_id) "
        "VALUES ('marathon_clip', 'Marathon', 'output', '/tmp/marathon_final.mp4', 50, '')"
    )
    # Insert failed Marathon release
    test_db.execute(
        "INSERT INTO releases (clip_id, channel, scheduled_at, status) "
        "VALUES ('failed_marathon', 'instagram_marathon', '2026-03-18T19:00:00Z', 'failed')"
    )
    test_db.commit()

    # ... call _substitute_failed_release with the failed release
    # Assert: substitute picks marathon_clip (game=Marathon), NOT deadlock_clip (higher score but wrong game)
```

**Step 3: Test approve_clips doesn't wipe processed clips**

```python
# tests/test_approve_clips_guard.py
"""_approve_clips must not bulk-skip clips that are already processed."""

def test_approve_preserves_processed_clips(test_db, test_config):
    """Clips with processed_path set must not be skipped."""
    test_db.execute(
        "INSERT INTO clips (id, game, status, processed_path, video_id) "
        "VALUES ('processed_no_upload', 'Deadlock', 'approved', '/tmp/final.mp4', '')"
    )
    test_db.execute(
        "INSERT INTO clips (id, game, status, processed_path, video_id) "
        "VALUES ('uploaded_clip', 'Deadlock', 'approved', '/tmp/final2.mp4', 'YT123')"
    )
    test_db.commit()

    # ... call _approve_clips
    # Assert: processed_no_upload stays 'approved' (not skipped)
    # Assert: uploaded_clip stays 'approved' (has video_id)
```

**Step 4: Test daily cap prevents double-up**

```python
# tests/test_daily_cap.py
"""Daily cap must prevent more clips than configured from being processed."""

def test_daily_cap_counts_all_channels(test_db, test_config):
    """count_output_shorts_today counts releases across channels for same game."""
    # Insert 7 releases for today across default + instagram_main
    # Assert: count_output_shorts_today returns 7
    # Assert: run_autopilot_workflow returns "daily_cap_reached"
```

**Step 5: Commit**

```bash
git add tests/
git commit -m "test: integration tests for substitute guard, approve guard, daily cap"
```

---

## Task 7: Wrap All External API Calls with Timeout + Retry

**Why:** Every external call (Twitch, YouTube, Instagram, TikTok, Gemini) can hang or fail. A single wrapper ensures consistent behavior.

**Files:**
- Create: `clipper/http.py` (retry wrapper)
- Modify: `clipper/fetch/twitch.py` (use wrapper)
- Modify: `clipper/upload/youtube.py` (already has socket timeout, formalize)
- Modify: `clipper/upload/instagram.py` (use wrapper)
- Modify: `clipper/upload/tiktok.py` (use wrapper)
- Modify: `clipper/upload/facebook.py` (use wrapper)

**Step 1: Create `clipper/http.py`**

```python
"""Resilient HTTP client with timeout, retry, and structured logging."""
import logging
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Default timeouts: (connect, read) in seconds
DEFAULT_TIMEOUT = (10, 60)
UPLOAD_TIMEOUT = (10, 300)

def resilient_session(
    *,
    retries: int = 3,
    backoff_factor: float = 1.0,
    status_forcelist: tuple = (500, 502, 503, 504, 429),
    timeout: tuple = DEFAULT_TIMEOUT,
) -> requests.Session:
    """Create a requests.Session with automatic retry and timeout."""
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=["GET", "POST", "PUT"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    # Monkey-patch send to enforce default timeout
    original_send = session.send
    def send_with_timeout(*args, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return original_send(*args, **kwargs)
    session.send = send_with_timeout

    return session
```

**Step 2: Use in upload modules**

Replace bare `requests.post/get/put` calls with the resilient session. Example for tiktok.py:

```python
from clipper.http import resilient_session, UPLOAD_TIMEOUT

# Replace: requests.post(url, ..., timeout=15)
# With:    session.post(url, ...)
# Where session = resilient_session(timeout=(10, 15))
```

**Step 3: Commit**

```bash
git add clipper/http.py clipper/upload/ clipper/fetch/
git commit -m "feat: resilient HTTP client wraps all external API calls

Automatic retry with exponential backoff for transient errors.
Consistent timeouts prevent indefinite hangs."
```

---

## Execution Order

| Task | Priority | Impact | Effort |
|------|----------|--------|--------|
| 1. Game-Channel Binding | **P0** | Prevents wrong-channel posts | 1-2 hours |
| 2. Resilient Cron | **P0** | Pipeline self-heals after morning failure | 1-2 hours |
| 3. Aggressive Cleanup | **P1** | Stops 43GB from growing | 1 hour |
| 4. Token Health Check | **P1** | Catches expiry before wasting hours | 30 min |
| 5. Health Report | **P2** | User stops asking "did it work?" | 1 hour |
| 6. Integration Tests | **P2** | Prevents regression on all above | 1-2 hours |
| 7. HTTP Retry Wrapper | **P2** | Fewer transient failures across the board | 1-2 hours |

**Critical path:** Tasks 1 and 2 fix the two issues the user called out (wrong channels + morning failures). Do those first.
