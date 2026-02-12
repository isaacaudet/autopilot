"""Smart title generation for YouTube clips."""

import hashlib
import re
import unicodedata

# Viral keywords → (expanded action phrase, hook text)
_KEYWORD_MAP = {
    "ace": ("INSANE ACE", "INSANE ACE"),
    "clutch": ("INSANE CLUTCH", "CLUTCH MOMENT"),
    "1v5": ("1V5 CLUTCH", "1V5 CLUTCH"),
    "1v4": ("1V4 CLUTCH", "1V4 CLUTCH"),
    "1v3": ("1V3 CLUTCH", "1V3 CLUTCH"),
    "rage": ("TILTED MOMENT", "TILTED"),
    "banned": ("SUSPENDED LIVE", "SUSPENDED"),
    "ban": ("SUSPENDED LIVE", "SUSPENDED"),
    "caught": ("GETS CAUGHT LIVE", "CAUGHT LIVE"),
    "exposed": ("GETS EXPOSED", "EXPOSED"),
    "hack": ("SUSPICIOUS PLAY", "SUS PLAY"),
    "cheat": ("SUSPICIOUS PLAY", "SUS PLAY"),
    "fail": ("EPIC FAIL", "EPIC FAIL"),
    "rip": ("RIP", "RIP"),
    "insane": ("INSANE PLAY", "INSANE"),
    "crazy": ("CRAZY PLAY", "CRAZY"),
    "godlike": ("GODLIKE PLAY", "GODLIKE"),
    "flick": ("INSANE FLICK", "INSANE FLICK"),
    "collat": ("INSANE COLLATERAL", "COLLATERAL"),
    "wallbang": ("WALLBANG", "WALLBANG"),
    "ninja": ("NINJA DEFUSE", "NINJA DEFUSE"),
    "toxic": ("UNHINGED MOMENT", "UNHINGED"),
    "troll": ("TROLLING", "TROLLING"),
    "donate": ("DONATION REACTION", "DONATION"),
    "jumpscare": ("JUMPSCARE", "JUMPSCARE"),
    "scream": ("SCREAMS", "SCREAMS"),
    "cry": ("EMOTIONAL MOMENT", "EMOTIONAL"),
    "win": ("HUGE WIN", "HUGE WIN"),
    "world record": ("WORLD RECORD", "WORLD RECORD"),
    "wr": ("WORLD RECORD", "WORLD RECORD"),
    "wipe": ("TEAM WIPE", "TEAM WIPE"),
    "snipe": ("INSANE SNIPE", "INSANE SNIPE"),
    "oneshot": ("ONE SHOT", "ONE SHOT"),
    "1 shot": ("ONE SHOT", "ONE SHOT"),
    "combo": ("INSANE COMBO", "INSANE COMBO"),
    "outplay": ("INSANE OUTPLAY", "OUTPLAY"),
    "solo": ("SOLO CARRY", "SOLO CARRY"),
    "carry": ("HARD CARRY", "HARD CARRY"),
    "broken": ("THIS IS BROKEN", "BROKEN"),
    "op": ("THIS IS OP", "THIS IS OP"),
    "nerf": ("NEEDS A NERF", "NEEDS A NERF"),
    "buff": ("BUFFED", "BUFFED"),
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
_GAME_TEMPLATES = [
    "{streamer} {action} IN {game}",
    "{streamer} {action} | {game}",
    "{streamer}: {action} IN {game}",
]

# Generic templates when no game context
_GENERIC_TEMPLATES = [
    "{streamer} {action}",
    "{streamer}: {action}",
]

# Fallback action words per game category
_GAME_ACTIONS = {
    "deadlock": "DEADLOCK MOMENT",
    "valorant": "VALORANT MOMENT",
    "counter-strike": "CS2 MOMENT",
    "league of legends": "LEAGUE MOMENT",
    "fortnite": "FORTNITE MOMENT",
    "overwatch 2": "OVERWATCH MOMENT",
    "apex legends": "APEX MOMENT",
    "minecraft": "MINECRAFT MOMENT",
    "gta v": "GTA MOMENT",
    "arc raiders": "ARC RAIDERS MOMENT",
    "just chatting": "LIVE MOMENT",
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
    # Collapse repeated punctuation (!!!!! → !)
    title = re.sub(r"([!?.])\1{2,}", r"\1\1", title)
    # Normalize whitespace
    title = " ".join(title.split())
    return title.strip()


def generate_title(clip: dict) -> str:
    """Produce a click-worthy YouTube title (under 90 chars, no #Shorts).

    Uses clip keys: title, streamer, game, id.
    """
    raw_title = clip.get("title", "")
    streamer = clip.get("streamer", "Unknown").upper()
    game = clip.get("game", "")
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
        # Title is garbage — generate from game + streamer
        game_lower = game.lower() if game else ""
        action = _GAME_ACTIONS.get(game_lower, f"{game} MOMENT" if game else "INSANE MOMENT")
        if game and game.lower() != "just chatting":
            result = f"{streamer} {action}"
        else:
            result = f"{streamer} {action}"
    else:
        # Title is decent — clean it up and frame it
        cleaned = _clean_title(raw_title).upper()
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
        action = _GAME_ACTIONS.get(game_lower, f"{game} MOMENT" if game else "INSANE MOMENT")
        result = f"{streamer} {action}"
        if len(result) > 90:
            result = result[:87] + "..."
    else:
        result = sanitized

    return result


def generate_hook_text(clip: dict) -> str:
    """Produce 2-4 word hook overlay text for the first 2 seconds.

    Returns short, punchy text like "INSANE ACE" or "WATCH THIS".
    """
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
