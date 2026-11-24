"""Hauptablauf: Clips finden -> herunterladen -> Untertitel -> 9:16 -> TikTok."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import shutil
from pathlib import Path

import requests
import yaml

from . import video

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state" / "posted.json"
WORK = ROOT / "work"
OUT = ROOT / "output"


def load_state() -> list[dict]:
    return json.loads(STATE.read_text()) if STATE.exists() else []


def save_state(state: list[dict]) -> None:
    STATE.parent.mkdir(exist_ok=True)
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    state = [s for s in state if dt.datetime.fromisoformat(s["posted_at"]) > cutoff]
    STATE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def game_tag(name: str) -> str:
    return {"Grand Theft Auto VI": "gta6", "Grand Theft Auto V": "gta5",
            "Just Chatting": "twitchclips", "": "twitch"}.get(
        name, re.sub(r"[^a-z0-9]", "", name.lower()))


def score(clip: dict, cfg: dict | None = None, focus_games: set | None = None) -> float:
    """Aufrufe pro Stunde seit Erstellung -> bevorzugt, was GERADE viral geht.
    Clips aus den Fokus-Spielen (z. B. GTA) bekommen einen Bonus."""
    created = dt.datetime.fromisoformat(clip["created_at"].replace("Z", "+00:00"))
    age_h = max((dt.datetime.now(dt.timezone.utc) - created).total_seconds() / 3600, 0.5)
    s = clip["view_count"] / age_h
    if age_h < 2:  # ganz frische Clips extra nach vorne (vor der Konkurrenz posten)
        s *= 1.3
    if cfg and focus_games and clip.get("game_id") in focus_games:
        s *= float(cfg.get("game_bonus", 1.0))
    if cfg and cfg.get("top_streamers_backfill"):
        tops = {x.lower() for x in (cfg.get("top_streamers") or [])}
        if clip.get("broadcaster_name", "").lower() in tops:
            s *= float(cfg.get("top_streamer_bonus", 3.0))
    return s


def find_candidates(cfg: dict, state: list[dict]) -> list[dict]:
    from .twitch import Twitch

    tw = Twitch()
    hours = cfg["lookback_hours"]
    langs = list(cfg.get("languages") or [])
    games = tw.game_ids(cfg.get("games", []))
    print("Fokus-Spiele:", games or "(keine gefunden)")
    clips: list[dict] = []

    # 1) Top-Clips aus den Fokus-Spielen (z. B. GTA)
    for gid in games:
        clips += tw.top_clips(game_id=gid, hours=hours)

    # 2) Feste Liste großer Streamer + 3) wer gerade live die meisten Zuschauer hat
    logins = [s.lower() for s in (cfg.get("streamers_allowlist") or [])]
    logins += [s.lower() for s in (cfg.get("top_streamers") or [])]
    broadcaster_ids = set(tw.user_ids(list(dict.fromkeys(logins)))) if logins else set()
    if cfg.get("live_top_n", 0):
        for lang in langs:
            for st in tw.live_streams(lang, cfg["live_top_n"]):
                broadcaster_ids.add(st["user_id"])
    print(f"Durchsuche {len(broadcaster_ids)} Streamer + {len(games)} Spiele …")
    for bid in broadcaster_ids:
        try:
            clips += tw.top_clips(broadcaster_id=bid, hours=hours, pages=1)
        except Exception as e:
            print("Streamer übersprungen:", bid, e)

    allow_only = {s.lower() for s in (cfg.get("streamers_allowlist") or [])}
    if cfg.get("only_top_streamers") and not cfg.get("top_streamers_backfill"):
        allow_only |= {s.lower() for s in (cfg.get("top_streamers") or [])}
    posted_ids = {s["id"] for s in state}
    day_ago = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)
    per_streamer: dict[str, int] = {}
    for s in state:
        if dt.datetime.fromisoformat(s["posted_at"]) > day_ago:
            per_streamer[s["broadcaster"]] = per_streamer.get(s["broadcaster"], 0) + 1

    block = {s.lower() for s in cfg.get("streamers_blocklist") or []}
    not_before = cfg.get("not_before")
    nb = dt.datetime.fromisoformat(str(not_before)) if not_before else None
    words_blocked = [re.escape(w) for w in (cfg.get("blocked_title_words") or [])
                     + (cfg.get("boring_title_words") or [])]
    bad_title = re.compile(r"(" + "|".join(words_blocked) + r")", re.I) if words_blocked else re.compile(r"(?!x)x")
    seen, out = set(), []
    for c in clips:
        if nb and dt.datetime.fromisoformat(c["created_at"].replace("Z", "+00:00")) < nb:
            continue
        if bad_title.search(c.get("title") or ""):
            continue
        login = c["broadcaster_name"].lower()
        if (c["id"] in posted_ids or c["id"] in seen or login in block
                or (allow_only and login not in allow_only)
                or (langs and c.get("language") not in langs)
                or (cfg.get("only_focus_games") and c.get("game_id") not in games)
                or c["view_count"] < cfg["min_views"]
                or not cfg["min_duration"] <= c["duration"] <= cfg["max_duration"]
                or per_streamer.get(login, 0) >= cfg["max_per_streamer_per_day"]):
            continue
        seen.add(c["id"])
        out.append(c)
    names = dict(games)
    try:
        names.update(tw.game_names([c.get("game_id") for c in out if c.get("game_id") not in names]))
    except Exception:
        pass
    for c in out:
        c["_game_name"] = names.get(c.get("game_id"), "")
    focus = set(games)
    out.sort(key=lambda c: score(c, cfg, focus), reverse=True)
    print(f"{len(clips)} Clips gefunden, {len(out)} passen zu den Filtern. Top 5:")
    for c in out[:5]:
        print(f"  {score(c, cfg, focus):8.0f}/h  {c['broadcaster_name']:<16} {c['view_count']:>7} Aufrufe  "
              f"{c['_game_name'][:20]:<20} {c['title'][:50]}")
    return out


JUNK = re.compile(r"^(kekw|lul|lol|omegalul|xd+|pog\w*|clip|wtf|haha\w*|w+|l+|\W+|\d+)$", re.I)


def good_title(raw: str) -> bool:
    t = raw.strip()
    return len(t) >= 10 and not JUNK.match(t) and len(t.split()) >= 2


def pick_title(clip: dict, words: list) -> str:
    """Twitch-Titel, falls brauchbar – sonst der erste gesprochene Satz."""
    if good_title(clip["title"]):
        return clip["title"].strip()
    spoken = " ".join(w for _, _, w in words[:12]).strip()
    return (spoken[:70].rsplit(" ", 1)[0] + "…") if len(spoken) > 70 else spoken


def pick_overlay_title(cfg: dict, clip: dict, title: str, words: list) -> str:
    """Titel IM Video: muss auch für Leute verständlich sein, die den Streamer nicht kennen.
    Twitch-Titel nur, wenn er aussagekräftig ist (>= 4 Wörter), sonst bestes Zitat, sonst Hook."""
    from .captions import best_quote
    t = (clip.get("title") or "").strip()
    if good_title(t) and len(t.split()) >= 4:
        return t
    q = best_quote(words)
    if q and 3 <= len(q.split()) <= 9:
        return f"„{q}“"
    cc = cfg.get("caption_engine") or {}
    hooks = ((cc.get("games") or {}).get(clip.get("_game_name", ""), {}).get("hooks")
             or cc.get("hooks") or [])
    return random.choice(hooks) if hooks else title


def build_caption(cfg: dict, clip: dict, title: str) -> str:
    lang = clip.get("language", "en")
    if lang == "de" and cfg.get("caption_engine"):
        from . import captions
        return captions.build(cfg, clip, title, clip.get("_words") or [])
    tpl = cfg["caption_templates"].get(lang, cfg["caption_templates"]["en"])
    hooks = (cfg.get("hooks") or {}).get(lang) or (cfg.get("hooks") or {}).get("en") or [""]
    gname = clip.get("_game_name", "")
    if lang == "de" and gname in (cfg.get("caption_templates_by_game") or {}):
        tpl = cfg["caption_templates_by_game"][gname]
    if lang == "de" and (cfg.get("hooks_by_game") or {}).get(gname):
        hooks = cfg["hooks_by_game"][gname]
    streamer = clip["broadcaster_name"]
    game = clip.get("_game_name", "") or "Twitch"
    text = tpl.format(hook=random.choice(hooks), title=title, streamer=streamer, game=game,
                      streamer_tag=re.sub(r"[^a-z0-9_]", "", streamer.lower()),
                      game_tag=game_tag(clip.get("_game_name", "")))
    seen, lines = set(), []
    for line in text.splitlines():  # Zeilen behalten, doppelte/leere Hashtags entfernen
        parts = []
        for word in line.split():
            if word.startswith("#"):
                if word.lower() in seen or len(word) < 2:
                    continue
                seen.add(word.lower())
            parts.append(word)
        line = " ".join(parts).strip(" |–-")
        if line:
            lines.append(line)
    return "\n".join(lines)[:2200]


def write_summary(clip: dict, caption: str, cover_ms: int) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    opt = f"Option {clip['_option']}: " if clip.get("_option") else ""
    with open(summary, "a", encoding="utf-8") as f:
        f.write(f"### {opt}{clip['broadcaster_name']} – {clip['view_count']} Aufrufe\n\n"
                f"**Beschreibung (kopieren):**\n```\n{caption}\n```\n"
                f"Titelbild: Sekunde {cover_ms / 1000:.1f} · Quelle: {clip['url']}\n\n")


def _clean_topic(raw: str) -> str:
    """Kanalname aus dem Secret: Leerzeichen/Zeilenumbrüche weg, ganze URL erlaubt."""
    t = raw.strip().strip("/")
    if "/" in t:  # z. B. "https://ntfy.sh/meinkanal" eingefügt
        t = t.rsplit("/", 1)[-1]
    return t


def notify(cfg: dict, caption: str, cover_ms: int, clip: dict, mode: str,
           mp4: Path | None = None) -> None:
    """Push aufs Handy (ntfy) + Zusammenfassung im GitHub-Lauf."""
    summary = os.environ.get("GITHUB_STEP_SUMMARY")

    def note(text: str) -> None:
        print(text)
        if summary:
            with open(summary, "a", encoding="utf-8") as f:
                f.write(f"> {text}\n\n")

    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            opt = f"Option {clip['_option']}: " if clip.get("_option") else ""
            f.write(f"### {opt}{clip['broadcaster_name']} – {clip['view_count']} Aufrufe\n\n"
                    f"**Beschreibung (kopieren):**\n```\n{caption}\n```\n"
                    f"Titelbild: Sekunde {cover_ms / 1000:.1f} · Quelle: {clip['url']}\n\n")
    topic = _clean_topic(os.environ.get("NTFY_TOPIC") or "")
    if not topic:
        note("⚠️ **Keine Push-Nachricht gesendet:** Secret `NTFY_TOPIC` fehlt.")
        return
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", topic):
        note("⚠️ **Keine Push-Nachricht gesendet:** Der Kanalname in `NTFY_TOPIC` enthält "
             "ungültige Zeichen. Erlaubt sind nur Buchstaben, Zahlen, `-` und `_` (max. 64).")
        return

    url = f"https://ntfy.sh/{topic}"
    streamer = clip["broadcaster_name"]
    run, repo = os.environ.get("GITHUB_RUN_ID"), os.environ.get("GITHUB_REPOSITORY")
    link = f"https://github.com/{repo}/actions/runs/{run}" if run and repo else ""
    prefix = "🧪 TEST – " if mode == "dry-run" else "🎬 "
    if clip.get("_option"):
        prefix += f"Option {clip['_option']} – "
    ok_video = ok_text = False
    errors = []

    def err(what: str, e: Exception, r=None) -> None:
        detail = ""
        if r is not None:
            detail = f" (HTTP {r.status_code}: {r.text.strip()[:200]})"
        elif isinstance(e, requests.RequestException) and e.response is not None:
            detail = f" (HTTP {e.response.status_code}: {e.response.text.strip()[:200]})"
        else:
            detail = f" ({type(e).__name__})"
        errors.append(what + detail.replace(topic, "***"))

    # 1) Video als Anhang
    if mode in ("manual", "dry-run") and mp4 and mp4.stat().st_size < 15 * 1024 * 1024:
        try:
            with open(mp4, "rb") as f:
                r = requests.put(url, data=f, timeout=180, params={
                    "filename": f"{streamer}_{clip['id'][:20]}.mp4",
                    "title": f"{prefix}Neuer Clip: {streamer} ({clip['view_count']} Aufrufe)",
                    "message": f"Video herunterladen → in TikTok hochladen. "
                               f"Titelbild: Sekunde {cover_ms / 1000:.1f}.",
                    "tags": "movie_camera"})
            if r.ok:
                ok_video = True
            else:
                err("Video-Anhang", None, r)
        except Exception as e:
            err("Video-Anhang", e)

    # 1b) Fallback: Nachricht mit Link zum Download auf GitHub
    if not ok_video:
        try:
            r = requests.post(url, timeout=30, data=(
                f"Video im GitHub-Lauf unter 'Artifacts' herunterladen:\n{link}\n"
                f"Titelbild: Sekunde {cover_ms / 1000:.1f}").encode("utf-8"),
                params={"title": f"{prefix}Neuer Clip: {streamer}", "click": link,
                        "tags": "movie_camera"})
            if not r.ok:
                err("Hinweis-Nachricht", None, r)
        except Exception as e:
            err("Hinweis-Nachricht", e)

    # 2) Beschreibung als eigene Nachricht -> lange drücken = kopieren
    try:
        r = requests.post(url, data=caption.encode("utf-8"), timeout=30,
                          params={"title": (f"📋 Beschreibung Option {clip['_option']}"
                                            if clip.get("_option") else "📋 Beschreibung")
                                           + " (lange drücken → kopieren)"})
        ok_text = r.ok
        if not r.ok:
            err("Beschreibung", None, r)
    except Exception as e:
        err("Beschreibung", e)

    if ok_video and ok_text:
        note("✅ Push-Nachrichten an ntfy gesendet (Video + Beschreibung).")
    elif ok_text:
        note("🟡 Beschreibung gesendet, Video nur als Link. Fehler: " + "; ".join(errors))
    else:
        note("❌ **Push an ntfy fehlgeschlagen:** " + "; ".join(errors))


def plan_cut(cfg: dict, dur: float, words: list, loud_s: float) -> tuple[float, float]:
    """Start/Länge festlegen: Leerlauf am Anfang weg, max. Länge um den Höhepunkt herum."""
    start = 0.0
    max_trim = float(cfg.get("max_start_trim", 4))
    if words and words[0][0] > 1.2:
        start = min(words[0][0] - 0.4, max_trim)
    max_len = float(cfg.get("max_output_seconds", 40))
    if dur - start > max_len:
        # Fenster so legen, dass der Höhepunkt drin ist und danach noch ~8 s Reaktion kommen
        start = min(max(start, loud_s + 8 - max_len), dur - max_len)
    length = min(dur - start, max_len)
    return round(start, 2), round(length, 2)


def produce(cfg: dict, src: Path, clip: dict, language: str | None,
            name: str) -> tuple[Path, str, int]:
    """Gibt (fertiges Video, Titel, Thumbnail-Zeitpunkt in ms) zurück."""
    dur = video.probe_duration(src)
    words = []
    if cfg.get("captions"):
        try:
            words = video.transcribe(src, language, cfg.get("whisper_model", "small"))
            print(f"{len(words)} Wörter transkribiert.")
        except Exception as e:  # Untertitel sind nice-to-have, kein Abbruchgrund
            print("Untertitel übersprungen:", e)
    loud_ms = video.find_cover_ms(src)
    start, length = plan_cut(cfg, dur, words, loud_ms / 1000)
    if start or length < dur:
        print(f"Schnitt: {start:.1f}s – {start + length:.1f}s (Original {dur:.1f}s)")
    # Wörter auf den neuen Zeitbereich verschieben
    words = [(a - start, e - start, w) for a, e, w in words if e > start and a < start + length]
    words = [(max(a, 0.0), min(e, length), w) for a, e, w in words]
    clip["_words"] = words
    title = pick_title(clip, words)
    overlay = pick_overlay_title(cfg, clip, title, words)
    facecam = None
    if cfg.get("split_layout", True):
        try:
            facecam = video.detect_facecam(src, start, length)
        except Exception as e:
            print("Facecam-Erkennung übersprungen:", e)
    print("Layout:", "Split (Facecam oben)" if facecam else "Standard", "| Titel im Video:", overlay)
    ass = video.make_ass(words, overlay, length, src.with_suffix(".ass"),
                         credit=(f"twitch.tv/{clip['broadcaster_name']}" if cfg.get("credit_overlay") else ""),
                         split=bool(facecam))
    OUT.mkdir(exist_ok=True)
    mp4 = video.render(src, ass, OUT / f"{name}.mp4", start=start, length=length,
                       facecam=facecam)
    cover_ms = int(min(max(loud_ms - start * 1000, 0), max(length * 1000 - 500, 0)))
    video.save_cover(mp4, cover_ms, OUT / f"{name}_thumbnail.jpg")
    print(f"Titel: {title} | Thumbnail bei {cover_ms / 1000:.1f}s")
    return mp4, title, cover_ms


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Nur Videos erstellen, nicht hochladen")
    ap.add_argument("--local-file", help="Test: lokale Datei statt Twitch-Clip schneiden")
    ap.add_argument("--title", default="Test Clip")
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())

    if args.local_file:
        fake = {"title": args.title, "broadcaster_name": "TestStreamer", "language": "de",
                "url": "https://twitch.tv/", "view_count": 0, "_game_name": "Grand Theft Auto VI"}
        mp4, title, cover_ms = produce(cfg, Path(args.local_file), fake, None, "local_test")
        print("Fertig:", mp4)
        print("Beschreibung:", build_caption(cfg, fake, title))
        return

    if not args.dry_run:
        from .gate import mark_run
        mark_run()  # Zeitpunkt gilt als erledigt, auch wenn kein Clip gefunden wird
    state = load_state()
    candidates = find_candidates(cfg, state)
    if not candidates:
        print("Kein neuer passender Clip. Ende.")
        return

    mode = "dry-run" if args.dry_run else cfg.get("tiktok_mode", "manual")
    if cfg.get("mode") == "compilation":
        from .compile import run_compilation
        run_compilation(cfg, args, state, candidates, mode)
        return
    token = None
    if mode in ("inbox", "direct"):
        from . import tiktok
        token, new_refresh = tiktok.refresh_access_token()
        if new_refresh != os.environ.get("TIKTOK_REFRESH_TOKEN"):
            print(f"::add-mask::{new_refresh}")
            (ROOT / ".new_refresh_token").write_text(new_refresh)

    done = 0
    new_clips: list[dict] = []
    total = int(cfg["clips_per_run"])
    used_streamers: set[str] = set()
    for clip in candidates:
        if done >= total:
            break
        if clip["broadcaster_name"].lower() in used_streamers:
            continue  # pro Lauf jeder Streamer nur einmal -> echte Auswahl
        print(f"\n▶ {clip['broadcaster_name']}: {clip['title']} "
              f"({clip['view_count']} Aufrufe, {clip['language']}) {clip['url']}")
        work = WORK / clip["id"]
        try:
            src = video.download(clip["url"], work)
            if cfg.get("nsfw_filter", True):
                bad, why = video.nsfw_check(src, strict=cfg.get("nsfw_strict", True))
                if bad:
                    print("⛔ Übersprungen (Nacktheit erkannt):", why)
                    continue
            mp4, title, cover_ms = produce(cfg, src, clip, clip.get("language"), clip["id"])
            caption = build_caption(cfg, clip, title)
            print("Beschreibung:", caption)
            if mode in ("inbox", "direct"):
                from . import tiktok
                tiktok.upload(token, mp4, caption, mode, cfg["privacy_level"], cover_ms)
            clip["_option"] = f"{done + 1}/{total}" if total > 1 else ""
            write_summary(clip, caption, cover_ms)
            new_clips.append({
                "id": clip["id"], "file": mp4.name, "thumb": f"{clip['id']}_thumbnail.jpg",
                "streamer": clip["broadcaster_name"], "views": clip["view_count"],
                "game": clip.get("_game_name", ""), "caption": caption,
                "cover_s": round(cover_ms / 1000, 1), "option": done + 1,
                "source": clip["url"], "test": mode == "dry-run",
                "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
            used_streamers.add(clip["broadcaster_name"].lower())
            state.append({"id": clip["id"], "broadcaster": clip["broadcaster_name"].lower(),
                          "posted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                          "url": clip["url"], "dry_run": args.dry_run})
            done += 1
        except Exception as e:
            print("Fehler bei diesem Clip, nehme den nächsten:", e)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    if not args.dry_run:
        save_state(state)
    OUT.mkdir(exist_ok=True)
    (OUT / "new_clips.json").write_text(json.dumps(new_clips, ensure_ascii=False, indent=2))
    print(f"\n{done} Clip(s) verarbeitet.")


if __name__ == "__main__":
    main()
