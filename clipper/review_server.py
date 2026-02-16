"""Minimal web server for visual clip review — approve/reject processed clips."""

import json
import threading
import webbrowser
from pathlib import Path

from rich.console import Console

console = Console()

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Clipper Review</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  .rejected { opacity: 0.3; }
  video { max-height: 400px; }
</style>
</head>
<body class="bg-gray-950 text-gray-100 min-h-screen">
<div class="max-w-6xl mx-auto px-4 py-8">
  <h1 class="text-2xl font-bold mb-2">Clip Review</h1>
  <p class="text-gray-400 mb-6">Toggle clips to approve/reject, then confirm.</p>

  <div id="clips" class="grid gap-6">
  </div>

  <div class="mt-8 flex gap-4 items-center">
    <button onclick="submit()" class="bg-cyan-600 hover:bg-cyan-500 text-white font-bold py-3 px-8 rounded-lg text-lg">
      Confirm Selection
    </button>
    <span id="count" class="text-gray-400"></span>
  </div>
</div>

<script>
const clips = __CLIPS_JSON__;
const approved = new Set(clips.map((_, i) => i));

function render() {
  const container = document.getElementById('clips');
  container.textContent = '';
  clips.forEach((clip, i) => {
    const isApproved = approved.has(i);
    const card = document.createElement('div');
    card.className = 'bg-gray-900 rounded-xl p-4 flex gap-4 cursor-pointer' + (isApproved ? '' : ' rejected');
    card.addEventListener('click', () => { toggle(i); });

    const videoUrl = '/video/' + encodeURIComponent(clip._review_id || i);

    // Build DOM elements safely using textContent
    const videoCol = document.createElement('div');
    videoCol.className = 'flex-shrink-0';
    const video = document.createElement('video');
    video.src = videoUrl;
    video.controls = true;
    video.preload = 'metadata';
    video.className = 'rounded-lg w-64';
    video.addEventListener('click', (e) => e.stopPropagation());
    videoCol.appendChild(video);

    const info = document.createElement('div');
    info.className = 'flex-1 min-w-0';

    const header = document.createElement('div');
    header.className = 'flex items-center gap-2 mb-1';
    const icon = document.createElement('span');
    icon.className = 'text-2xl';
    icon.textContent = isApproved ? '\\u2705' : '\\u274C';
    const title = document.createElement('h2');
    title.className = 'text-lg font-semibold truncate';
    title.textContent = clip.title || '?';
    header.appendChild(icon);
    header.appendChild(title);

    const streamer = document.createElement('p');
    streamer.className = 'text-cyan-400';
    streamer.textContent = clip.streamer || '?';

    const meta = document.createElement('p');
    meta.className = 'text-gray-400 text-sm';
    meta.textContent = (clip.game || '') + ' \\u00B7 ' + (clip.duration || 0).toFixed(0) + 's \\u00B7 ' + (clip.view_count || 0).toLocaleString() + ' views';

    const score = document.createElement('p');
    score.className = 'text-gray-500 text-sm mt-1';
    score.textContent = 'Score: ' + (clip._score || 0).toFixed(0);

    info.appendChild(header);
    info.appendChild(streamer);
    info.appendChild(meta);
    info.appendChild(score);

    card.appendChild(videoCol);
    card.appendChild(info);
    container.appendChild(card);
  });
  document.getElementById('count').textContent = approved.size + ' of ' + clips.length + ' approved';
}

function toggle(i) {
  if (approved.has(i)) approved.delete(i);
  else approved.add(i);
  render();
}

function submit() {
  const indices = Array.from(approved).sort((a, b) => a - b);
  fetch('/confirm', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({approved: indices}),
  }).then(() => {
    document.body.textContent = '';
    const msg = document.createElement('div');
    msg.className = 'flex items-center justify-center h-screen text-2xl text-green-400';
    msg.textContent = 'Done! You can close this tab.';
    document.body.appendChild(msg);
  });
}

render();
</script>
</body>
</html>"""


def run_review_server(
    clips: list[dict], config: dict, port: int = 8432
) -> list[dict]:
    """Serve a review page, block until user approves. Returns approved clips.

    Falls back to terminal review if bottle is unavailable or server fails.
    """
    try:
        return _serve_review(clips, config, port)
    except Exception as e:
        console.print(f"[yellow]Review server failed ({e}), falling back to terminal review.[/yellow]")
        return _terminal_review(clips)


def _serve_review(clips: list[dict], config: dict, port: int) -> list[dict]:
    """Run a bottle server for visual review."""
    import bottle

    app = bottle.Bottle()
    result: list[int] = []
    done = threading.Event()

    # Assign review IDs so we can map video requests
    for i, clip in enumerate(clips):
        clip["_review_id"] = str(i)

    # Build HTML with clip data embedded
    safe_clips = []
    for clip in clips:
        safe_clips.append({
            "title": clip.get("title", ""),
            "streamer": clip.get("streamer", ""),
            "game": clip.get("game", ""),
            "duration": clip.get("duration", 0),
            "view_count": clip.get("view_count", 0),
            "_score": clip.get("_score", 0),
            "_review_id": clip.get("_review_id", ""),
            "processed_path": clip.get("processed_path", ""),
        })

    html = _HTML_TEMPLATE.replace("__CLIPS_JSON__", json.dumps(safe_clips))

    @app.route("/")
    def index():
        return html

    @app.route("/video/<review_id>")
    def serve_video(review_id):
        try:
            idx = int(review_id)
        except ValueError:
            bottle.abort(404)
            return
        if idx < 0 or idx >= len(clips):
            bottle.abort(404)
            return
        video_path = clips[idx].get("processed_path", "")
        if not video_path or not Path(video_path).exists():
            bottle.abort(404)
            return
        return bottle.static_file(
            Path(video_path).name,
            root=str(Path(video_path).parent),
            mimetype="video/mp4",
        )

    @app.route("/confirm", method="POST")
    def confirm():
        data = bottle.request.json
        result.clear()
        result.extend(data.get("approved", []))
        done.set()
        return {"ok": True}

    # Start server in background thread
    server_thread = threading.Thread(
        target=lambda: bottle.run(app, host="localhost", port=port, quiet=True),
        daemon=True,
    )
    server_thread.start()

    url = f"http://localhost:{port}"
    console.print(f"[bold]Opening review at {url}[/bold]")
    webbrowser.open(url)

    # Block until user confirms
    done.wait()

    # Return approved clips
    approved = [clips[i] for i in sorted(result) if 0 <= i < len(clips)]
    console.print(f"\n[bold]{len(approved)} approved, {len(clips) - len(approved)} rejected.[/bold]")
    return approved


def _terminal_review(clips: list[dict]) -> list[dict]:
    """Fallback terminal review: show list, let user reject by number."""
    import questionary

    console.print("\n[bold]Review processed clips:[/bold]")
    for i, clip in enumerate(clips, 1):
        console.print(
            f"  {i}. [cyan]{clip.get('streamer', '?')}[/cyan] — "
            f"{clip.get('title', '?')[:40]} (score {clip.get('_score', 0):.0f})"
        )

    reject = questionary.text(
        "Reject clips by number (comma-separated), or Enter to approve all:"
    ).ask()

    if not reject or reject.strip() == "":
        return clips

    try:
        reject_indices = {int(x.strip()) - 1 for x in reject.split(",")}
    except ValueError:
        console.print("[yellow]Invalid input, approving all.[/yellow]")
        return clips

    approved = [c for i, c in enumerate(clips) if i not in reject_indices]
    console.print(f"[bold]{len(approved)} approved, {len(reject_indices)} rejected.[/bold]")
    return approved
