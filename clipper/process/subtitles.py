"""Transcribe video audio to ASS subtitles with word-level timing."""

import json
import re
import threading
from pathlib import Path

from rich.console import Console
from clipper.layout_profiles import load_facecam_profiles, normalize_layout_tuning

console = Console()

_PROFANITY = {
    "fuck", "fucking", "fucked", "fucker", "shit", "shitting", "shitty",
    "bitch", "bitches", "ass", "asshole", "damn", "dammit", "goddamn",
    "hell", "crap", "bastard", "piss", "pissed", "cock", "dick",
    "pussy", "whore", "slut", "bollocks",
}


def _censor_words(all_words: list[dict]) -> tuple[list[dict], list[tuple[float, float]]]:
    """Replace profane words with f*** style and collect mute time ranges."""
    import string
    censored = []
    mute_ranges: list[tuple[float, float]] = []
    for w in all_words:
        text = w.get("word", "")
        stripped = text.strip(string.punctuation).lower()
        if stripped in _PROFANITY:
            clean = text[0] + "***" if text else "***"
            censored.append({**w, "word": clean})
            mute_ranges.append((w.get("start", 0), w.get("end", 0)))
        else:
            censored.append(w)
    return censored, mute_ranges


_TUNING_KEYS = (
    "safe_top_ratio",
    "safe_bottom_ratio",
    "facecam_band_ratio",
    "gameplay_zoom",
    "gameplay_zoom_no_facecam",
    "gameplay_x_bias",
    "gameplay_y_bias",
    "hud_height_ratio",
    "hud_scale",
    "hud_x_ratio",
    "hud_y_ratio",
    "title_y_ratio",
    "subtitle_margin_ratio",
)


def _effective_layout_profile(config: dict, clip: dict | None) -> dict:
    """Return streamer profile merged with optional per-clip layout override."""
    merged: dict = {}
    if clip:
        streamer = str(clip.get("streamer") or "").strip().lower()
        if streamer:
            prof = load_facecam_profiles(config).get(streamer)
            if isinstance(prof, dict):
                merged.update(prof)

        override = clip.get("_layout_override")
        if isinstance(override, dict):
            if override.get("facecam_enabled") is not None:
                merged["facecam_enabled"] = bool(override.get("facecam_enabled"))
            if override.get("hud_enabled") is not None:
                merged["hud_enabled"] = bool(override.get("hud_enabled"))
            tuning_override = {k: override.get(k) for k in _TUNING_KEYS if k in override}
            merged.update(normalize_layout_tuning(tuning_override))
    return merged

# Serializes Whisper calls — Numba's workqueue threading layer is not
# thread-safe and aborts when accessed from multiple threads concurrently.
_transcribe_lock = threading.Lock()

# Module-level Whisper model cache — avoids reloading for each clip
_cached_model = None
_cached_model_name = None


def _get_or_load_model(model_name: str, device: str):
    """Load faster-whisper model, reusing cached instance if same model+device."""
    global _cached_model, _cached_model_name
    key = f"{model_name}:{device}"
    if _cached_model is not None and _cached_model_name == key:
        return _cached_model
    console.print(f"[blue]Loading Whisper model:[/blue] {model_name} on {device}")
    from faster_whisper import WhisperModel
    compute_type = "float16" if device == "cuda" else "int8"
    _cached_model = WhisperModel(model_name, device=device, compute_type=compute_type)
    _cached_model_name = key
    return _cached_model


# ASS header template for Shorts-style subtitles
# MarginV ~450 places text in the lower-center safe zone (not at the very bottom)
# Bold + large font + thick outline = readable on any background
ASS_HEADER = """[Script Info]
Title: Clipper Subtitles
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Impact,{fontsize},&H00FFFF00,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,2,0,1,{outline},2,2,40,40,{marginv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# ASS header for regular (non-Shorts) videos
# PlayResX/Y must be set so the ASS renderer positions text correctly
# regardless of the source video resolution.
ASS_HEADER_REGULAR = """[Script Info]
Title: Clipper Subtitles
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Impact,56,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,2,0,1,5,2,2,20,20,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _format_ass_time(seconds: float) -> str:
    """Convert seconds to ASS timestamp format (H:MM:SS.cc)."""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int((seconds % 1) * 100)
    return f"{hrs}:{mins:02d}:{secs:02d}.{centis:02d}"


def _group_words_into_phrases(words: list[dict], max_words: int = 3, max_duration: float = 2.5) -> list[dict]:
    """Group word-level segments into short readable phrases.

    Each phrase is 2-3 words, shown for a natural duration.
    Returns list of {start, end, text, words} dicts where words preserves
    individual word timing for highlight animation.
    """
    if not words:
        return []

    phrases = []
    current_words = []
    current_word_timings = []
    current_start = None

    for word_info in words:
        word = word_info.get("word", "").strip()
        if not word:
            continue

        start = word_info.get("start", 0)
        end = word_info.get("end", 0)

        if current_start is None:
            current_start = start

        current_words.append(word)
        current_word_timings.append({"word": word, "start": start, "end": end})
        current_end = end

        # Flush phrase when we hit max words, max duration, or sentence-ending punctuation
        duration = current_end - current_start
        is_sentence_end = word.rstrip()[-1] in ".!?," if word.rstrip() else False

        if len(current_words) >= max_words or duration >= max_duration or is_sentence_end:
            phrases.append({
                "start": current_start,
                "end": current_end,
                "text": " ".join(current_words),
                "words": list(current_word_timings),
            })
            current_words = []
            current_word_timings = []
            current_start = None

    # Flush remaining words
    if current_words and current_start is not None:
        phrases.append({
            "start": current_start,
            "end": current_end,
            "text": " ".join(current_words),
            "words": list(current_word_timings),
        })

    # Enforce minimum duration per phrase.
    # Keep this short to avoid pushing subsequent lines late.
    MIN_DURATION = 0.35
    for phrase in phrases:
        if phrase["end"] - phrase["start"] < MIN_DURATION:
            phrase["end"] = phrase["start"] + MIN_DURATION

    # Fix overlaps while minimizing subtitle delay:
    # try shortening the previous phrase first; only delay current if necessary.
    GAP = 0.03
    for i in range(1, len(phrases)):
        prev = phrases[i - 1]
        curr = phrases[i]
        if curr["start"] < prev["end"] + GAP:
            target_prev_end = curr["start"] - GAP
            min_prev_end = prev["start"] + MIN_DURATION
            if target_prev_end >= min_prev_end:
                prev["end"] = target_prev_end
            else:
                curr["start"] = prev["end"] + GAP
                if curr["end"] <= curr["start"]:
                    curr["end"] = curr["start"] + MIN_DURATION

    # Deduplicate phrases with near-identical text and overlapping times
    deduped = []
    for phrase in phrases:
        if deduped:
            prev = deduped[-1]
            # Skip if same text and timestamps are within 1s of each other
            if (phrase["text"].lower() == prev["text"].lower()
                    and abs(phrase["start"] - prev["start"]) < 1.0):
                continue
        deduped.append(phrase)

    return deduped


def _apply_phrase_lead_in(phrases: list[dict], lead_in_seconds: float) -> list[dict]:
    """Shift subtitle starts slightly earlier to reduce perceived lag."""
    if lead_in_seconds <= 0 or not phrases:
        return phrases

    min_visible = 0.25
    gap = 0.02
    adjusted: list[dict] = []
    for phrase in phrases:
        start = max(0.0, float(phrase.get("start", 0)) - lead_in_seconds)
        end = max(float(phrase.get("end", start)), start + min_visible)
        item = dict(phrase)
        item["start"] = start
        item["end"] = end
        adjusted.append(item)

    # Resolve overlaps with the same "avoid delay" strategy.
    for i in range(1, len(adjusted)):
        prev = adjusted[i - 1]
        curr = adjusted[i]
        if curr["start"] < prev["end"] + gap:
            target_prev_end = curr["start"] - gap
            min_prev_end = prev["start"] + min_visible
            if target_prev_end >= min_prev_end:
                prev["end"] = target_prev_end
            else:
                curr["start"] = prev["end"] + gap
                if curr["end"] <= curr["start"]:
                    curr["end"] = curr["start"] + min_visible

    return adjusted


def _build_highlight_text(phrase: dict) -> str:
    """Build ASS text with word-by-word highlight using karaoke-style override tags.

    Each word starts white, then the 'current' word is highlighted yellow
    using \\kf (smooth karaoke fill) timing.
    """
    words = phrase.get("words", [])
    if not words:
        return phrase["text"].upper()

    phrase_start = phrase["start"]
    parts = []

    for winfo in words:
        word = winfo["word"].upper()
        # Duration of this word in centiseconds (for \kf tag)
        word_dur_cs = max(1, int((winfo["end"] - winfo["start"]) * 100))
        # \kf = smooth fill karaoke: fills text from secondary to primary color
        # \1c = primary (filled) color, \2c = secondary (unfilled) color
        parts.append(f"{{\\kf{word_dur_cs}}}{word}")

    return " ".join(parts)


def _generate_ass_events(phrases: list[dict], highlight: bool = True, pop_animation: bool = True) -> str:
    """Generate ASS dialogue events from phrases.

    If highlight=True, uses karaoke tags for word-by-word color fill.
    If pop_animation=True, adds a scale-in pop effect to each line.
    """
    lines = []
    for phrase in phrases:
        start = _format_ass_time(phrase["start"])
        end = _format_ass_time(phrase["end"])

        if highlight and phrase.get("words"):
            text = _build_highlight_text(phrase)
        else:
            text = phrase["text"].upper()

        text = text.replace("\n", "\\N")

        # Pop-in animation: scale from 0% -> 105% in 80ms -> settle to 100% in next 70ms
        if pop_animation:
            pop = "{\\fscx0\\fscy0\\t(0,80,\\fscx105\\fscy105)\\t(80,150,\\fscx100\\fscy100)}"
            text = pop + text

        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    return "\n".join(lines)


def _resolve_subtitle_margin_v(config: dict, clip: dict | None = None) -> int:
    """Resolve Shorts subtitle vertical margin from config + optional streamer profile."""
    shorts_cfg = config.get("shorts", {}) or {}
    fill_cfg = shorts_cfg.get("fill", {}) if isinstance(shorts_cfg.get("fill"), dict) else {}
    h = int(shorts_cfg.get("height", 1920) or 1920)
    margin_v = float(shorts_cfg.get("subtitle_margin_v", 450) or 450)
    base_ratio = margin_v / float(max(1, h))
    ratio = float(fill_cfg.get("subtitle_margin_ratio", base_ratio) or base_ratio)

    merged = normalize_layout_tuning({"subtitle_margin_ratio": ratio})

    prof = _effective_layout_profile(config, clip)
    if prof:
        merged.update(normalize_layout_tuning(prof))

    out = int(round(float(merged.get("subtitle_margin_ratio", base_ratio)) * h))
    return max(20, min(h - 20, out))


def _check_segment_quality(segment) -> str | None:
    """Check a Whisper segment for hallucination indicators.

    Returns a reason string if the segment is suspicious, or None if it looks fine.
    Only catches clear hallucinations — NOT low-confidence real speech.
    Gaming audio has loud SFX that tanks confidence even when speech is real,
    so thresholds are deliberately loose. Whisper's own no_speech_threshold
    already does the initial filtering.

    Accepts both faster-whisper Segment objects (attribute access) and dicts.
    """
    if isinstance(segment, dict):
        avg_logprob = segment.get("avg_logprob", 0)
        compression_ratio = segment.get("compression_ratio", 0)
        no_speech_prob = segment.get("no_speech_prob", 0)
    else:
        avg_logprob = getattr(segment, "avg_logprob", 0)
        compression_ratio = getattr(segment, "compression_ratio", 0)
        no_speech_prob = getattr(segment, "no_speech_prob", 0)

    # Only filter truly hallucinated loops (very high compression = repeated text)
    if compression_ratio > 2.8:
        return f"Repetitive/loopy text (compression: {compression_ratio:.2f})"
    # Only filter when Whisper is VERY sure there's no speech
    if no_speech_prob > 0.9:
        return f"Likely not speech (no_speech_prob: {no_speech_prob:.2f})"
    # Only filter absurdly low confidence (real speech in noisy gaming audio
    # often has logprob around -0.8 to -1.0, so don't filter those)
    if avg_logprob < -1.5:
        return f"Extremely low confidence (logprob: {avg_logprob:.2f})"
    return None


def _review_flagged_segments(
    flagged: list[dict], all_words: list[dict], interactive: bool
) -> list[dict] | None:
    """Review flagged segments and apply user decisions to the word list.

    In interactive mode, prompts user for each flagged segment.
    In non-interactive mode, returns None to signal that review is needed.

    Returns the filtered word list, or None if the clip should be skipped
    or review is deferred (non-interactive).
    """
    if not interactive:
        return None  # caller writes .review file

    for item in flagged:
        seg = item["segment"]
        reason = item["reason"]
        text = seg.get("text", "").strip()
        start = seg.get("start", 0)
        end = seg.get("end", 0)

        console.print(f"\n[yellow]  Suspicious transcription found:[/yellow]")
        console.print(f"    [{start:.1f}s - {end:.1f}s] \"{text}\"")
        console.print(f"    {reason}")
        console.print(f"    [dim](k)eep as-is  (d)elete segment  (e)dit text  (s)kip entire clip[/dim]")

        while True:
            try:
                choice = input("    > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return None  # treat as skip
            if choice in ("k", "d", "e", "s"):
                break
            console.print("    [dim]Enter k, d, e, or s[/dim]")

        if choice == "s":
            console.print("[yellow]  Skipping clip.[/yellow]")
            return None
        elif choice == "d":
            # Remove words that fall within this segment's time range
            all_words = [
                w for w in all_words
                if not (w.get("start", 0) >= start and w.get("end", 0) <= end)
            ]
        elif choice == "e":
            try:
                new_text = input("    Replacement text: ").strip()
            except (EOFError, KeyboardInterrupt):
                return None
            if new_text:
                # Remove old words in range, insert single replacement word with original timing
                all_words = [
                    w for w in all_words
                    if not (w.get("start", 0) >= start and w.get("end", 0) <= end)
                ]
                all_words.append({"word": new_text, "start": start, "end": end})
                all_words.sort(key=lambda w: w.get("start", 0))
        # choice == "k": keep as-is, do nothing

    return all_words


def transcribe(
    video_path: Path,
    config: dict,
    clip: dict | None = None,
    verbose: bool = False,
) -> tuple[Path | None, list[dict] | None, list]:
    """Transcribe a video file and generate an ASS subtitle file.

    Uses word-level timestamps for precise, short phrase display.
    Returns (ass_path, all_words, censor_ranges) tuple — word data enables downstream LLM analysis.
    Either or both may be None on failure.
    """
    try:
        from faster_whisper import WhisperModel  # noqa: F401
    except ImportError:
        console.print(
            "[red]Error: faster-whisper is not installed. "
            "Install it with: pip install faster-whisper[/red]"
        )
        return None, None, []

    video_path = Path(video_path)
    sub_config = config.get("subtitles", {})
    model_name = sub_config.get("whisper_model", "large-v3")

    console.print(f"[blue]Transcribing:[/blue] {video_path.name} (model: {model_name})")

    # faster-whisper: use CUDA if available, otherwise CPU with int8
    device = "cpu"
    try:
        import torch
        if torch.cuda.is_available():
            device = "cuda"
    except ImportError:
        pass

    if verbose:
        console.print(f"  [dim]Whisper device: {device}[/dim]")

    # Serialize transcription for thread safety
    with _transcribe_lock:
        try:
            model = _get_or_load_model(model_name, device)
            segments_iter, info = model.transcribe(
                str(video_path),
                word_timestamps=True,
                language="en",
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
                compression_ratio_threshold=2.8,
                initial_prompt="Gaming stream clip with voice commentary.",
                vad_filter=True,
                vad_parameters={
                    "min_speech_duration_ms": 200,
                    "min_silence_duration_ms": 400,
                    "speech_pad_ms": 300,
                },
            )
            segments = list(segments_iter)
        except Exception as e:
            console.print(f"[red]Transcription failed:[/red] {e}")
            return None, None, []

    # Extract word-level timestamps from faster-whisper Segment objects
    all_words = []
    for segment in segments:
        words = segment.words
        if words:
            all_words.extend([
                {"word": w.word, "start": w.start, "end": w.end}
                for w in words
            ])
        else:
            text = segment.text.strip()
            if text:
                all_words.append({
                    "word": text,
                    "start": segment.start,
                    "end": segment.end,
                })

    if not all_words:
        console.print("[yellow]No speech detected in video.[/yellow]")
        return None, None, []

    def _is_gibberish_token(token: str) -> bool:
        t = str(token or "").strip()
        if not t:
            return True
        core = re.sub(r"^\\W+|\\W+$", "", t, flags=re.UNICODE)
        if not core:
            return True
        if len(core) > 24:
            return True
        if re.search(r"(.)\\1{4,}", core.lower()):
            return True
        # If it's mostly non-ASCII, it's unlikely to be intended English dialogue.
        ascii_ratio = sum(1 for c in core if ord(c) < 128) / max(1, len(core))
        if ascii_ratio < 0.6:
            return True
        alpha = [c for c in core if c.isalpha()]
        if len(alpha) >= 6 and not re.search(r"[aeiou]", core.lower()):
            return True
        return False

    # Filter obviously gibberish tokens while keeping a safety net (don't delete too much).
    words_before = len(all_words)
    gibberish_filtered = [w for w in all_words if not _is_gibberish_token(w.get("word", ""))]
    if gibberish_filtered and len(gibberish_filtered) >= words_before * 0.6:
        removed = words_before - len(gibberish_filtered)
        if removed > 0 and verbose:
            console.print(f"  [dim]Removed {removed} gibberish token(s) before phrase grouping[/dim]")
        all_words = gibberish_filtered

    # Auto-filter obvious hallucinations (delete segments, never skip the clip)
    # Safety net: if filtering would remove >70% of words, keep everything —
    # Whisper's own filtering is usually sufficient for gaming audio.
    words_before = len(all_words)
    filtered_words = list(all_words)

    for segment in segments:
        reason = _check_segment_quality(segment)
        if reason:
            start = segment.start
            end = segment.end
            if verbose:
                console.print(f"  [dim]Flagged segment [{start:.1f}s-{end:.1f}s]: {reason}[/dim]")
            filtered_words = [
                w for w in filtered_words
                if not (w.get("start", 0) >= start and w.get("end", 0) <= end)
            ]

    # Safety net: don't nuke all speech — gaming audio triggers false positives.
    # Exception: if ALL segments were flagged, trust the filter (it's a real hallucination).
    if filtered_words and len(filtered_words) >= words_before * 0.3:
        all_words = filtered_words
        removed = words_before - len(all_words)
        if removed > 0 and verbose:
            console.print(f"  [dim]Filtered {removed} suspicious words, kept {len(all_words)}[/dim]")
    elif words_before > 0 and not filtered_words:
        # Every segment was flagged — blanket hallucination, suppress subtitles.
        console.print("[yellow]All transcription segments flagged as hallucinations — suppressing subtitles.[/yellow]")
        return None, None, []
    elif words_before > 0:
        if verbose:
            console.print(f"  [dim]Filter too aggressive ({len(filtered_words)}/{words_before} remaining) — keeping original[/dim]")
        # keep all_words as-is

    # Clip-level repetition check: ≤2 unique words across 4+ total words is a
    # hallucination (e.g. "cancel cancel cancel..."). Individual segments may look
    # fine but the whole transcript is just one word repeated.
    if len(all_words) >= 4:
        unique_words = {
            re.sub(r'\W+', '', w.get('word', '')).lower()
            for w in all_words
            if re.sub(r'\W+', '', w.get('word', '')).strip()
        }
        if len(unique_words) <= 2:
            console.print(
                f"[yellow]Suppressing subtitles: only {len(unique_words)} unique word(s) "
                f"across {len(all_words)} tokens {unique_words} — likely hallucination.[/yellow]"
            )
            return None, None, []

    all_words, censor_ranges = _censor_words(all_words)

    # Group words into short phrases
    max_words = sub_config.get("words_per_phrase", 3)
    phrases = _group_words_into_phrases(all_words, max_words=max_words)
    lead_in_ms = int(sub_config.get("lead_in_ms", 80))
    phrases = _apply_phrase_lead_in(phrases, max(0, lead_in_ms) / 1000.0)

    if verbose:
        console.print(f"  {len(all_words)} words -> {len(phrases)} phrases")

    # Determine if this is a Shorts video (check dimensions or config hint)
    is_shorts = config.get("_current_is_shorts", True)  # default to Shorts style

    # Generate ASS file
    fontsize = sub_config.get("font_size", 34)
    outline = sub_config.get("outline_width", 4)
    margin_v = _resolve_subtitle_margin_v(config, clip)

    if is_shorts:
        header = ASS_HEADER.format(fontsize=fontsize, outline=outline, marginv=margin_v)
    else:
        header = ASS_HEADER_REGULAR

    events = _generate_ass_events(phrases, highlight=is_shorts, pop_animation=is_shorts)

    ass_path = video_path.with_suffix(".ass")
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(header)
        f.write(events)
        f.write("\n")

    console.print(f"[green]Subtitles saved:[/green] {ass_path.name} ({len(phrases)} phrases)")
    return ass_path, all_words, censor_ranges
