"""Transcribe video audio to ASS subtitles with word-level timing."""

import json
import threading
from pathlib import Path

from rich.console import Console

console = Console()

# Serializes Whisper calls — Numba's workqueue threading layer is not
# thread-safe and aborts when accessed from multiple threads concurrently.
_transcribe_lock = threading.Lock()

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
Style: Default,Impact,36,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,2,0,1,4,2,2,20,20,50,1

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

    # Enforce minimum duration per phrase (at least 0.5s visible)
    MIN_DURATION = 0.5
    for phrase in phrases:
        if phrase["end"] - phrase["start"] < MIN_DURATION:
            phrase["end"] = phrase["start"] + MIN_DURATION

    # Fix overlapping phrases — each phrase must start after the previous one ends
    for i in range(1, len(phrases)):
        if phrases[i]["start"] < phrases[i - 1]["end"]:
            # If this phrase starts before the previous one ends, push it forward
            gap = 0.05  # 50ms gap between phrases
            phrases[i]["start"] = phrases[i - 1]["end"] + gap
            # Also ensure end is still after start
            if phrases[i]["end"] <= phrases[i]["start"]:
                phrases[i]["end"] = phrases[i]["start"] + MIN_DURATION

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


def _check_segment_quality(segment: dict) -> str | None:
    """Check a Whisper segment for hallucination indicators.

    Returns a reason string if the segment is suspicious, or None if it looks fine.
    Only catches clear hallucinations — NOT low-confidence real speech.
    Gaming audio has loud SFX that tanks confidence even when speech is real,
    so thresholds are deliberately loose. Whisper's own no_speech_threshold
    already does the initial filtering.
    """
    avg_logprob = segment.get("avg_logprob", 0)
    compression_ratio = segment.get("compression_ratio", 0)
    no_speech_prob = segment.get("no_speech_prob", 0)

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


def transcribe(video_path: Path, config: dict, verbose: bool = False) -> Path | None:
    """Transcribe a video file and generate an ASS subtitle file.

    Uses word-level timestamps for precise, short phrase display.
    Returns the Path to the .ass file, or None on failure.
    """
    try:
        import whisper
    except ImportError:
        console.print(
            "[red]Error: openai-whisper is not installed. "
            "Install it with: pip install openai-whisper[/red]"
        )
        return None

    video_path = Path(video_path)
    sub_config = config.get("subtitles", {})
    model_name = sub_config.get("whisper_model", "turbo")

    console.print(f"[blue]Transcribing:[/blue] {video_path.name} (model: {model_name})")

    # Detect Apple Silicon MPS for GPU acceleration (falls back to CPU)
    device = "cpu"
    try:
        import torch
        if torch.backends.mps.is_available():
            device = "mps"
    except Exception:
        pass

    if verbose:
        console.print(f"  [dim]Whisper device: {device}[/dim]")

    # Serialize transcription — Numba's workqueue layer is not thread-safe
    # and crashes when multiple Whisper calls run concurrently.
    with _transcribe_lock:
        try:
            model = whisper.load_model(model_name, device=device)
            result = model.transcribe(
                str(video_path),
                word_timestamps=True,
                language="en",
                condition_on_previous_text=False,  # prevents hallucinated repetitions
                no_speech_threshold=0.7,           # relaxed — gaming audio is noisy, don't over-filter
                compression_ratio_threshold=2.8,   # relaxed — only reject obvious hallucinated loops
                initial_prompt="Gaming stream clip with voice commentary.",
                verbose=verbose,
                fp16=(device != "cpu"),
            )
        except Exception as e:
            console.print(f"[red]Transcription failed:[/red] {e}")
            return None

    # Extract word-level timestamps
    all_words = []
    for segment in result.get("segments", []):
        words = segment.get("words", [])
        if words:
            all_words.extend(words)
        else:
            # Fallback: use segment-level timing if no word timestamps
            text = segment.get("text", "").strip()
            if text:
                all_words.append({
                    "word": text,
                    "start": segment["start"],
                    "end": segment["end"],
                })

    if not all_words:
        console.print("[yellow]No speech detected in video.[/yellow]")
        return None

    # Auto-filter obvious hallucinations (delete segments, never skip the clip)
    # Safety net: if filtering would remove >70% of words, keep everything —
    # Whisper's own filtering is usually sufficient for gaming audio.
    words_before = len(all_words)
    filtered_words = list(all_words)

    for segment in result.get("segments", []):
        reason = _check_segment_quality(segment)
        if reason:
            start = segment.get("start", 0)
            end = segment.get("end", 0)
            if verbose:
                console.print(f"  [dim]Flagged segment [{start:.1f}s-{end:.1f}s]: {reason}[/dim]")
            filtered_words = [
                w for w in filtered_words
                if not (w.get("start", 0) >= start and w.get("end", 0) <= end)
            ]

    # Safety net: don't nuke all speech — gaming audio triggers false positives
    if filtered_words and len(filtered_words) >= words_before * 0.3:
        all_words = filtered_words
        removed = words_before - len(all_words)
        if removed > 0 and verbose:
            console.print(f"  [dim]Filtered {removed} suspicious words, kept {len(all_words)}[/dim]")
    elif words_before > 0 and not filtered_words:
        if verbose:
            console.print(f"  [dim]Filter would remove all {words_before} words — keeping original[/dim]")
        # keep all_words as-is
    elif words_before > 0:
        if verbose:
            console.print(f"  [dim]Filter too aggressive ({len(filtered_words)}/{words_before} remaining) — keeping original[/dim]")
        # keep all_words as-is

    # Group words into short phrases
    max_words = sub_config.get("words_per_phrase", 3)
    phrases = _group_words_into_phrases(all_words, max_words=max_words)

    if verbose:
        console.print(f"  {len(all_words)} words -> {len(phrases)} phrases")

    # Determine if this is a Shorts video (check dimensions or config hint)
    is_shorts = config.get("_current_is_shorts", True)  # default to Shorts style

    # Generate ASS file
    fontsize = sub_config.get("font_size", 34)
    outline = sub_config.get("outline_width", 4)
    margin_v = config.get("shorts", {}).get("subtitle_margin_v", 450)

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
    return ass_path
