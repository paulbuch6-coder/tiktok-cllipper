"""Abwechslungsreiche Beschreibungen, die aus dem echten Clip entstehen.

- wechselnde Satz-Strukturen (Hook, Zitat, POV, Frage …)
- echtes Zitat aus den Untertiteln („…“)
- Frage/Aufforderung am Ende -> mehr Kommentare
- Suchbegriffe + wechselnde Hashtags
- merkt sich die letzten Hooks/Fragen/Strukturen und wiederholt sie nicht
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECENT = ROOT / "state" / "recent_captions.json"


def _load_recent() -> dict:
    try:
        return json.loads(RECENT.read_text())
    except Exception:
        return {}


def _save_recent(rec: dict) -> None:
    RECENT.parent.mkdir(exist_ok=True)
    RECENT.write_text(json.dumps({k: v[-40:] for k, v in rec.items()}, ensure_ascii=False))


def _pick(options: list[str], key: str, rec: dict, avoid: int) -> str:
    """Zufällig wählen, aber nichts aus den letzten `avoid` Malen."""
    if not options:
        return ""
    used = rec.get(key, [])[-avoid:]
    fresh = [o for o in options if o not in used] or options
    choice = random.choice(fresh)
    rec.setdefault(key, []).append(choice)
    return choice


def best_quote(words: list[tuple[float, float, str]]) -> str:
    """Den 'zitierfähigsten' gesprochenen Satz finden (kurz, emotional)."""
    if not words:
        return ""
    sentences, cur = [], []
    for _, _, w in words:
        cur.append(w)
        if w.endswith((".", "!", "?")) or len(cur) >= 12:
            sentences.append(" ".join(cur))
            cur = []
    if cur:
        sentences.append(" ".join(cur))

    def score(s: str) -> float:
        n = len(s.split())
        if n < 3 or n > 12:
            return -1
        sc = 0.0
        sc += 3 * s.count("!") + 2 * s.count("?")
        sc += sum(1 for t in s.split() if len(t) > 2 and t.isupper())
        sc += 2 if 4 <= n <= 9 else 0
        sc += 2 if re.search(r"\b(alter|digga|bro|was|wie|nein|junge|krass|safe|ey)\b", s, re.I) else 0
        return sc

    best = max(sentences, key=score)
    if score(best) < 0:
        return ""
    best = best.strip().strip("\"„“")
    return best[0].upper() + best[1:] if best else ""


def build(cfg: dict, clip: dict, title: str, words: list) -> str:
    cc = cfg.get("caption_engine") or {}
    rec = _load_recent()
    gname = clip.get("_game_name", "")
    game_cfg = (cc.get("games") or {}).get(gname, {})
    streamer = clip["broadcaster_name"]
    stag = "#" + re.sub(r"[^a-z0-9_]", "", streamer.lower())
    game_kw = game_cfg.get("keyword") or gname or "Twitch"

    quote = best_quote(words)
    title = (title or "").strip()
    if title and quote and title.lower().startswith(quote.lower()[:20]):
        title = ""  # Titel ist eh der gesprochene Satz -> nicht doppelt

    hooks = (game_cfg.get("hooks") or []) + (cc.get("hooks") or [])
    hook = _pick(hooks, "hook", rec, avoid=15)
    cta = _pick(cc.get("ctas") or [""], "cta", rec, avoid=8)
    emoji = random.choice(cc.get("emojis") or ["💀"])

    structures = [s for s in (cc.get("structures") or ["{hook} {title}\n{cta}"])
                  if ("{quote}" not in s or quote) and ("{title}" not in s or title)]
    struct = _pick(structures, "structure", rec, avoid=3)
    text = struct.format(hook=hook, title=title, quote=quote, streamer=streamer, cta=cta,
                         e=emoji, game=game_kw)

    kw_line = _pick(cc.get("keyword_lines") or ["{streamer} {game} deutsch"], "kw", rec,
                    avoid=2).format(streamer=streamer, game=game_kw)

    core = game_cfg.get("hashtags_core") or cc.get("hashtags_core") or []
    pool = list(game_cfg.get("hashtags_pool") or cc.get("hashtags_pool") or [])
    random.shuffle(pool)
    tags, seen = [], set()
    for t in core + [stag] + pool:
        if t.lower() not in seen and len(tags) < int(cc.get("max_hashtags", 5)):
            tags.append(t)
            seen.add(t.lower())

    _save_recent(rec)
    lines = [re.sub(r"[ \t]+", " ", l).strip(" |–-") for l in text.splitlines()]
    lines = [l for l in lines if l] + [kw_line, " ".join(tags)]
    return "\n".join(lines)[:2200]
