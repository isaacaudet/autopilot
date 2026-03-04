"""LLM-powered clip transcript analysis. Gemini Flash → Ollama fallback."""

import datetime as _dt
import json
import os

from rich.console import Console

console = Console()


def _clip_age_hours(clip: dict) -> float:
    """Return clip age in hours. Kept local to avoid circular import with score.py."""
    created = str(clip.get("created_at", "") or "").strip()
    if not created:
        return 24.0
    try:
        created_dt = _dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
        now = _dt.datetime.now(_dt.timezone.utc)
        return max(0.5, (now - created_dt).total_seconds() / 3600.0)
    except (ValueError, TypeError):
        return 24.0


_PRE_SCREEN_PROMPT = """You are a YouTube Shorts virality expert for gaming clips.
Rate the viral potential of each clip based ONLY on its metadata (no video).

Clips:
{clip_list}

For each, assess:
- Title viral signal: specific hype words (clutch, 1v5, ace, rage, banned) vs generic (character names, single letters, stream titles)
- Moment type: clutch/fail/funny/rage/hype/skill vs boring/filler
- View traction: views given clip age (fresh 0-view clips are OK; old low-view clips are not)
- Duration: 15-58s ideal for Shorts; penalize > 90s

Score guide:
10: Unmistakably viral (hype title + known streamer + strong views)
7-9: Clear potential (2+ strong signals)
5-6: Average (one weak signal or generic content)
3-4: Weak (generic title + low views)
1-2: Reject (single word/letter title, zero traction, stream title spam)

Respond ONLY as JSON: {{"clip_id_1": 8, "clip_id_2": 3, ...}}"""


def pre_analyze_clips(clips: list[dict]) -> dict[str, int]:
    """Batch pre-screen clips via a single Gemini text call (no download needed).

    Returns {clip_id: score_1_to_10} dict. Falls back silently to {} on any failure.
    """
    if not clips:
        return {}

    lines = []
    for c in clips:
        age = _clip_age_hours(c)
        lines.append(
            f"- id={c.get('id', '?')} | streamer={c.get('streamer', '?')} | "
            f"game={c.get('game', '?')} | title=\"{str(c.get('title', ''))[:60]}\" | "
            f"dur={float(c.get('duration', 0) or 0):.0f}s | "
            f"views={int(c.get('view_count', 0) or 0)} | age={age:.1f}h"
        )

    prompt = _PRE_SCREEN_PROMPT.format(clip_list="\n".join(lines))

    try:
        text = _call_gemini(prompt)
        if not text:
            return {}
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        raw = json.loads(text)
        if not isinstance(raw, dict):
            return {}
        result: dict[str, int] = {}
        for k, v in raw.items():
            try:
                score = max(1, min(10, int(v)))
                result[str(k)] = score
            except (TypeError, ValueError):
                pass
        return result
    except Exception:
        return {}

_TEXT_PROMPT_TEMPLATE = """Analyze this gaming stream clip transcript for entertainment value.

Streamer: {streamer}
Game: {game}
Clip title: {title}
Duration: {duration}s
Views: {views}

Transcript:
{transcript}

Respond with ONLY a JSON object (no markdown fences):
{{
  "entertainment_score": <1-10 integer>,
  "category": "<clutch|fail|funny|hype|rage|skill|commentary|other>",
  "best_quote": "<most entertaining/memorable quote from transcript, max 30 chars>",
  "moment_timestamp": <approximate seconds into clip where the peak moment happens>,
  "summary": "<one sentence describing what happens>",
  "title_variants": ["<click-worthy YouTube title 1, max 70 chars>", "<title 2>", "<title 3>"],
  "hook_text": "<2-4 word hook overlay text for video intro, e.g. IMPOSSIBLE SAVE>"
}}"""

_VIDEO_PROMPT_TEMPLATE = """Watch this gaming stream clip and analyze it for entertainment value.

Streamer: {streamer}
Game: {game}
Clip title: {title}
Duration: {duration}s
Views: {views}

Consider gameplay intensity, streamer reactions, chat energy, comedic timing, and visual spectacle.

Respond with ONLY a JSON object (no markdown fences):
{{
  "entertainment_score": <1-10 integer>,
  "category": "<clutch|fail|funny|hype|rage|skill|commentary|other>",
  "best_quote": "<most entertaining/memorable quote from transcript, max 30 chars>",
  "moment_timestamp": <approximate seconds into clip where the peak moment happens>,
  "summary": "<one sentence describing what happens>",
  "title_variants": ["<click-worthy YouTube title 1, max 70 chars>", "<title 2>", "<title 3>"],
  "hook_text": "<2-4 word hook overlay text for video intro, e.g. IMPOSSIBLE SAVE>",
  "visual_energy": <1-10 integer, how visually intense/dynamic the clip is>,
  "retention_prediction": <0-100 integer, estimated percent of viewers watching to the end>
}}"""


def _parse_llm_response(text: str) -> dict | None:
    """Parse and validate LLM JSON response."""
    text = text.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    result = json.loads(text)

    if not isinstance(result.get("entertainment_score"), (int, float)):
        return None
    result["entertainment_score"] = max(1, min(10, int(result["entertainment_score"])))

    valid_categories = {"clutch", "fail", "funny", "hype", "rage", "skill", "commentary", "other"}
    if result.get("category") not in valid_categories:
        result["category"] = "other"

    # Validate title_variants — list of strings, each ≤70 chars
    variants = result.get("title_variants")
    if isinstance(variants, list):
        result["title_variants"] = [
            str(v)[:70] for v in variants if isinstance(v, str) and v.strip()
        ][:3]
    else:
        result.pop("title_variants", None)

    # Validate hook_text — short string for overlay
    hook = result.get("hook_text")
    if isinstance(hook, str) and hook.strip():
        result["hook_text"] = hook.strip()[:30]
    else:
        result.pop("hook_text", None)

    # Validate video-analysis fields (optional)
    if "visual_energy" in result:
        try:
            result["visual_energy"] = max(1, min(10, int(result["visual_energy"])))
        except (TypeError, ValueError):
            result.pop("visual_energy", None)

    if "retention_prediction" in result:
        try:
            result["retention_prediction"] = max(0, min(100, int(result["retention_prediction"])))
        except (TypeError, ValueError):
            result.pop("retention_prediction", None)

    return result


def _call_gemini(prompt: str) -> str | None:
    """Try Gemini 2.5 Flash with text. Returns raw response text or None."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None

    try:
        from google import genai
    except ImportError:
        return None

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    return response.text


def _call_gemini_video(video_path: str, prompt: str) -> str | None:
    """Try Gemini 2.5 Flash with video upload. Returns raw response text or None."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None

    try:
        from google import genai
    except ImportError:
        return None

    client = genai.Client(api_key=api_key)
    video_file = client.files.upload(file=video_path)
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[video_file, prompt],
    )
    return response.text


def _call_ollama(prompt: str, model: str = "gemma3:4b") -> str | None:
    """Try local Ollama. Returns raw response text or None."""
    import urllib.request
    import urllib.error

    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())["response"]
    except (urllib.error.URLError, OSError, KeyError, json.JSONDecodeError):
        return None


def analyze_clip(
    clip: dict,
    transcript_text: str,
    verbose: bool = False,
    video_path: str | None = None,
) -> dict | None:
    """Analyze a clip for entertainment scoring.

    If video_path is provided, tries Gemini video-native analysis first (richer signal).
    Falls back to text-only Gemini, then local Ollama.
    Returns dict with entertainment_score, category, best_quote, moment_timestamp,
    summary — or None on all failures. Never blocks the pipeline.
    """
    clip_meta = dict(
        streamer=clip.get("streamer", "unknown"),
        game=clip.get("game", "unknown"),
        title=clip.get("title", ""),
        duration=clip.get("duration", 0),
        views=clip.get("view_count", 0),
    )

    # Try video-native Gemini analysis first (best signal)
    if video_path and os.path.exists(video_path):
        try:
            video_prompt = _VIDEO_PROMPT_TEMPLATE.format(**clip_meta)
            text = _call_gemini_video(video_path, video_prompt)
            if text:
                result = _parse_llm_response(text)
                if result:
                    if verbose:
                        console.print(
                            f"  [magenta]LLM (Gemini video):[/magenta] score={result['entertainment_score']}/10 "
                            f"cat={result['category']} visual={result.get('visual_energy', '?')}/10"
                        )
                    return result
        except Exception as e:
            if verbose:
                console.print(f"  [dim]Gemini video failed: {e}[/dim]")

    # Text-only fallback
    transcript = transcript_text[:3000]
    text_prompt = _TEXT_PROMPT_TEMPLATE.format(transcript=transcript, **clip_meta)

    # Try Gemini text
    try:
        text = _call_gemini(text_prompt)
        if text:
            result = _parse_llm_response(text)
            if result:
                if verbose:
                    console.print(
                        f"  [magenta]LLM (Gemini text):[/magenta] score={result['entertainment_score']}/10 "
                        f"cat={result['category']} quote=\"{result.get('best_quote', '')[:30]}\""
                    )
                return result
    except Exception as e:
        if verbose:
            console.print(f"  [dim]Gemini text failed: {e}[/dim]")

    # Fallback to Ollama
    try:
        text = _call_ollama(text_prompt)
        if text:
            result = _parse_llm_response(text)
            if result:
                if verbose:
                    console.print(
                        f"  [magenta]LLM (Ollama):[/magenta] score={result['entertainment_score']}/10 "
                        f"cat={result['category']} quote=\"{result.get('best_quote', '')[:30]}\""
                    )
                return result
    except Exception as e:
        if verbose:
            console.print(f"  [dim]Ollama failed: {e}[/dim]")

    if verbose:
        console.print("  [dim]No LLM available — skipping analysis[/dim]")
    return None
