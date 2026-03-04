"""Smart title generation for YouTube clips."""

import html
import hashlib
import re
import unicodedata

# Viral keywords → (expanded action phrase in Title Case, hook text for overlay)
_KEYWORD_MAP = {
    "ace": ("Insane Ace", "INSANE ACE"),
    "clutch": ("Insane Clutch", "CLUTCH MOMENT"),
    "1v5": ("1v5 Clutch", "1V5 CLUTCH"),
    "1v4": ("1v4 Clutch", "1V4 CLUTCH"),
    "1v3": ("1v3 Clutch", "1V3 CLUTCH"),
    "rage": ("Tilted Moment", "TILTED"),
    "banned": ("Suspended Live", "SUSPENDED"),
    "ban": ("Suspended Live", "SUSPENDED"),
    "caught": ("Gets Caught Live", "CAUGHT LIVE"),
    "exposed": ("Gets Exposed", "EXPOSED"),
    "hack": ("Suspicious Play", "SUS PLAY"),
    "cheat": ("Suspicious Play", "SUS PLAY"),
    "fail": ("Epic Fail", "EPIC FAIL"),
    "rip": ("RIP", "RIP"),
    "insane": ("Insane Play", "INSANE"),
    "crazy": ("Crazy Play", "CRAZY"),
    "godlike": ("Godlike Play", "GODLIKE"),
    "flick": ("Insane Flick", "INSANE FLICK"),
    "collat": ("Insane Collateral", "COLLATERAL"),
    "wallbang": ("Wallbang", "WALLBANG"),
    "ninja": ("Ninja Defuse", "NINJA DEFUSE"),
    "toxic": ("Unhinged Moment", "UNHINGED"),
    "troll": ("Trolling", "TROLLING"),
    "donate": ("Donation Reaction", "DONATION"),
    "jumpscare": ("Jumpscare", "JUMPSCARE"),
    "scream": ("Screams", "SCREAMS"),
    "cry": ("Emotional Moment", "EMOTIONAL"),
    "win": ("Huge Win", "HUGE WIN"),
    "world record": ("World Record", "WORLD RECORD"),
    "wr": ("World Record", "WORLD RECORD"),
    "wipe": ("Team Wipe", "TEAM WIPE"),
    "snipe": ("Insane Snipe", "INSANE SNIPE"),
    "oneshot": ("One Shot", "ONE SHOT"),
    "1 shot": ("One Shot", "ONE SHOT"),
    "combo": ("Insane Combo", "INSANE COMBO"),
    "outplay": ("Insane Outplay", "OUTPLAY"),
    "solo": ("Solo Carry", "SOLO CARRY"),
    "carry": ("Hard Carry", "HARD CARRY"),
    "broken": ("This Is Broken", "BROKEN"),
    "op": ("This Is OP", "THIS IS OP"),
    "nerf": ("Needs a Nerf", "NEEDS A NERF"),
    "buff": ("Buffed", "BUFFED"),
}

# --- Title Sanitization ---

# Words that should cause the title to be fully rejected and regenerated.
# Slurs, hate speech, extreme violence, sexual content, drugs.
_BLOCKLIST = {
    # Slurs / hate speech
    "nigger", "nigga", "faggot", "fag", "retard", "retarded", "tranny",
    "kike", "spic", "chink", "wetback", "coon", "gook", "dyke",
    "cracker", "beaner", "gringo", "paki", "raghead", "towelhead",
    # Extreme violence
    "murder", "murderer", "rape", "rapist", "molest", "pedophile",
    "genocide", "torture", "dismember", "decapitate", "mutilate",
    "lynching", "lynch",
    # Sexual
    "porn", "hentai", "orgasm", "masturbat", "blowjob", "handjob",
    "cum", "pussy", "cock", "dick", "penis", "vagina", "dildo",
    "anal", "nude", "naked", "boob", "tits",
    # Hate / extremism
    "hitler", "nazi", "kkk", "holocaust", "swastika", "jihad",
    "terrorist", "terrorism",
    # Drugs (hard)
    "cocaine", "heroin", "meth", "fentanyl", "overdose",
}

# Gaming-specific word replacements: in-game terminology → YouTube-safe alternatives.
# Applied with word-boundary matching to avoid mangling substrings.
_GAMING_REPLACEMENTS = {
    "kill": "elim",
    "killed": "eliminated",
    "kills": "elims",
    "killer": "slayer",
    "killing": "eliminating",
    "destroy": "dominate",
    "destroyed": "dominated",
    "destroys": "dominates",
    "headshot": "one tap",
    "dead": "down",
    "death": "wipeout",
    "deaths": "wipeouts",
    "blood": "intense",
    "bloody": "intense",
    "nuke": "mega streak",
    "slaughter": "domination",
    "slaughtered": "dominated",
    "shoot": "hit",
    "shoots": "hits",
    "shooting": "hitting",
    "gun": "weapon",
    "guns": "weapons",
    "knife": "melee",
    "knifed": "melee'd",
    "bomb": "blast",
    "massacre": "sweep",
    "victim": "target",
    "victims": "targets",
    "suicide": "self-elim",
    "die": "fall",
    "died": "fell",
    "dies": "falls",
    "dying": "falling",
}


def sanitize_title(title: str) -> str | None:
    """Sanitize a title for YouTube safety.

    Returns the cleaned title with gaming words replaced, or None if the title
    contains blocklisted words (caller should regenerate from game/streamer).
    """
    lower = title.lower()

    # Check blocklist — any match means full rejection
    for word in _BLOCKLIST:
        if re.search(rf"\b{re.escape(word)}", lower):
            return None

    # Apply gaming replacements with word-boundary matching (case-insensitive)
    result = title
    for old, new in _GAMING_REPLACEMENTS.items():
        pattern = re.compile(rf"\b{re.escape(old)}\b", re.IGNORECASE)
        if pattern.search(result):
            def _replace_preserve_case(m: re.Match, _new=new) -> str:
                matched = m.group(0)
                if matched.isupper():
                    return _new.upper()
                if matched[0].isupper():
                    return _new[0].upper() + _new[1:]
                return _new
            result = pattern.sub(_replace_preserve_case, result)

    return result

# Game-specific title templates: {streamer}, {action}, {game} available
# Action-first format — puts the hook before the streamer name (matches organic viral titles)
_GAME_TEMPLATES = [
    "{action} | {streamer} {game}",
    "{action} in {game} | {streamer}",
    "{streamer} {action} | {game}",
]

# Generic templates when no game context
_GENERIC_TEMPLATES = [
    "{action} | {streamer}",
    "{streamer} {action}",
]

# Fallback action words per game category (Title Case)
_GAME_ACTIONS = {
    "deadlock": "Deadlock Moment",
    "valorant": "Valorant Moment",
    "counter-strike": "CS2 Moment",
    "league of legends": "League Moment",
    "fortnite": "Fortnite Moment",
    "overwatch 2": "Overwatch Moment",
    "apex legends": "Apex Moment",
    "minecraft": "Minecraft Moment",
    "gta v": "GTA Moment",
    "arc raiders": "Arc Raiders Moment",
    "just chatting": "Live Moment",
}


def _is_garbage_title(title: str) -> bool:
    """Check if a Twitch clip title is low-quality / garbage."""
    if len(title) < 4:
        return True
    # Mostly non-ASCII (non-English)
    if not _is_english_text(title):
        return True
    # Single repeated character
    if len(set(title.strip().lower())) <= 2:
        return True
    return False


def _is_english_text(text: str) -> bool:
    """Check if text is predominantly English/ASCII.

    Returns False for Japanese, Korean, Chinese, Arabic, Cyrillic, etc.
    Short titles (<4 chars) are considered ambiguous and return True
    (they get handled by garbage title detection instead).
    """
    if len(text) < 4:
        return True
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    return ascii_chars / len(text) >= 0.5


def is_english_clip(clip: dict) -> bool:
    """Check if a clip is likely English based on title and streamer name.

    Clips with non-English titles are filtered out unless they contain
    a recognized viral keyword (e.g. Japanese title with 'ACE' in it
    still gets processed with a generated English title).
    """
    title = clip.get("title", "")

    # If title contains a viral keyword, keep it — we'll generate a good title
    if _find_keyword(title):
        return True

    # Short/empty titles are ambiguous — let them through (garbage filter handles them)
    if len(title) < 4:
        return True

    return _is_english_text(title)


def _pick_template(templates: list[str], clip_id: str) -> str:
    """Deterministically pick a template based on clip ID hash."""
    h = int(hashlib.md5(clip_id.encode()).hexdigest(), 16)
    return templates[h % len(templates)]


def _find_keyword(title: str) -> tuple[str, str] | None:
    """Scan title for viral keywords. Returns (action, hook_text) or None."""
    title_lower = title.lower()
    # Check multi-word keywords first (longer matches win)
    for kw in sorted(_KEYWORD_MAP, key=len, reverse=True):
        if kw in title_lower:
            return _KEYWORD_MAP[kw]
    return None


def _clean_title(title: str) -> str:
    """Strip excessive punctuation, normalize whitespace."""
    title = html.unescape(str(title or ""))
    # Drop URLs and trailing hashtags (e.g. #shorts #viral) from source titles.
    title = re.sub(r"https?://\S+", "", title, flags=re.IGNORECASE)
    title = re.sub(r"(?:^|\s)#[A-Za-z0-9_]+", " ", title)

    # Collapse repeated punctuation (!!!!! → !)
    title = re.sub(r"([!?.])\1{2,}", r"\1\1", title)
    # Normalize long dash separators into a stable token.
    title = re.sub(r"\s*[|/]+\s*", " | ", title)
    # Normalize whitespace
    title = " ".join(title.split())
    cleaned = title.strip(" \"'|:-")

    # If source is mostly all-caps, convert to title case for readability.
    letters = [c for c in cleaned if c.isalpha()]
    if letters:
        upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
        if upper_ratio > 0.85:
            cleaned = cleaned.title()

    return cleaned.strip()


def generate_title(clip: dict) -> str:
    """Produce a click-worthy YouTube title (under 90 chars, no #Shorts).

    Prefers LLM-generated title_variants when available. Falls back to
    keyword-based generation.

    Uses clip keys: title, streamer, game, id, _analysis.
    """
    streamer = clip.get("streamer", "Unknown")
    game = clip.get("game", "")

    def _add_context(base: str) -> str:
        """Append streamer/game context when it fits and isn't redundant."""
        base = _clean_title(base)
        parts: list[str] = []
        if streamer and streamer.lower() not in base.lower():
            parts.append(streamer)
        if game and game.lower() not in base.lower() and game.lower() != "just chatting":
            parts.append(game)
        if not parts:
            return base

        with_both = f"{base} | {' '.join(parts)}"
        if len(with_both) <= 90:
            return with_both

        if streamer and streamer.lower() not in base.lower():
            with_streamer = f"{base} | {streamer}"
            if len(with_streamer) <= 90:
                return with_streamer

        return base

    # Try LLM title variants first
    # LLM titles are already YouTube-safe — only check blocklist, skip gaming word replacements.
    # Preserve Gemini's intended casing (don't convert all-caps to title case).
    analysis = clip.get("_analysis", {})
    variants = analysis.get("title_variants", [])
    if variants:
        for v in variants:
            v = v.strip().strip('"\'')
            if not v:
                continue
            lower = v.lower()
            if any(re.search(rf"\b{re.escape(w)}", lower) for w in _BLOCKLIST):
                continue
            # Append streamer/game context if not already present and it fits
            parts = []
            if streamer and streamer.lower() not in lower:
                parts.append(streamer)
            if game and game.lower() not in lower and game.lower() != "just chatting":
                parts.append(game)
            candidate = v
            if parts:
                with_ctx = f"{v} | {' '.join(parts)}"
                if len(with_ctx) <= 90:
                    candidate = with_ctx
            if len(candidate) <= 90:
                return candidate

    raw_title = _clean_title(clip.get("title", ""))
    clip_id = clip.get("id", raw_title)

    keyword_match = _find_keyword(raw_title)

    if keyword_match:
        action, _ = keyword_match
        if game and game.lower() != "just chatting":
            template = _pick_template(_GAME_TEMPLATES, clip_id)
            result = template.format(streamer=streamer, action=action, game=game)
        else:
            template = _pick_template(_GENERIC_TEMPLATES, clip_id)
            result = template.format(streamer=streamer, action=action)
    elif _is_garbage_title(raw_title):
        # Title is garbage — prefer LLM quote over generic game action
        best_quote = clip.get("_analysis", {}).get("best_quote", "")
        if best_quote and len(best_quote) <= 30:
            action = best_quote.title()
        else:
            game_lower = game.lower() if game else ""
            action = _GAME_ACTIONS.get(game_lower, f"{game} Moment" if game else "Insane Moment")
        # Append game to streamer only if not already in the action phrase
        if game and game.lower() != "just chatting" and game.lower() not in action.lower():
            result = f"{action} | {streamer} {game}"
        else:
            result = f"{action} | {streamer}"
    else:
        # Title is decent — clean it up, keep original casing
        cleaned = _clean_title(raw_title)
        if streamer.lower() not in cleaned.lower():
            cleaned = f"{streamer}: {cleaned}"
        if game and game.lower() not in cleaned.lower() and game.lower() != "just chatting":
            cleaned = f"{cleaned} | {game}"
        result = cleaned

    # Hard cap at 90 chars (leave room for #Shorts suffix added later)
    if len(result) > 90:
        result = result[:87] + "..."

    # Sanitize: replace gaming words, reject blocklisted titles
    sanitized = sanitize_title(result)
    if sanitized is None:
        # Title contained blocklisted word — regenerate from game + streamer
        game_lower = game.lower() if game else ""
        action = _GAME_ACTIONS.get(game_lower, f"{game} Moment" if game else "Insane Moment")
        result = f"{action} | {streamer}"
        if len(result) > 90:
            result = result[:87] + "..."
    else:
        result = sanitized

    return result


def generate_hook_text(clip: dict) -> str:
    """Produce 2-4 word hook overlay text for the first 2 seconds.

    Returns short, punchy text like "INSANE ACE" or "WATCH THIS".
    Prefers LLM hook_text, falls back to best_quote, then keyword extraction.
    """
    override = clip.get("_hook_text_override")
    if override:
        return override.upper()

    analysis = clip.get("_analysis", {})

    # LLM hook_text takes top priority (purpose-built for overlay)
    hook = analysis.get("hook_text", "")
    if hook and len(hook) <= 30:
        return hook.upper()

    # LLM best_quote as fallback if concise enough for overlay
    best_quote = analysis.get("best_quote", "")
    if best_quote and len(best_quote) <= 30:
        return best_quote.upper()

    raw_title = clip.get("title", "")
    game = clip.get("game", "")

    keyword_match = _find_keyword(raw_title)
    if keyword_match:
        _, hook = keyword_match
        return hook

    # Game-specific fallback
    game_lower = game.lower() if game else ""
    if game_lower in _GAME_ACTIONS:
        return _GAME_ACTIONS[game_lower]

    return "WATCH THIS"
