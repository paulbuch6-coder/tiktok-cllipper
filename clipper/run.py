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
    return {"Grand Theft Auto VI": "gta6", "Grand Theft Auto V": "gtarp"}.get(
        name, re.sub(r"[^a-z0-9]", "", name.lower()))


def score(clip: dict) -> float:
    """Aufrufe pro Stunde seit Erstellung -> bevorzugt, was GERADE viral geht."""
    created = dt.datetime.fromisoformat(clip["created_at"].replace("Z", "+00:00"))
    age_h = max((dt.datetime.now(dt.timezone.utc) - created).total_seconds() / 3600, 0.5)
    return clip["view_count"] / age_h


def find_candidates(cfg: dict, state: list[dict]) -> list[dict]:
    from .twitch import Twitch

    tw = Twitch()
    games = tw.game_ids(cfg.get("games", []))
    print("Spiele:", games or "(keine gefunden)")
    clips: list[dict] = []
    allow = [s.lower() for s in cfg.get("streamers_allowlist") or []]
    if allow:
        for uid in tw.user_ids(allow):
            clips += tw.top_clips(broadcaster_id=uid, hours=cfg["lookback_hours"], pages=1)
    else:
        for gid in games:
            clips += tw.top_clips(game_id=gid, hours=cfg["lookback_hours"])

    posted_ids = {s["id"] for s in state}
    day_ago = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)
    per_streamer: dict[str, int] = {}
    for s in state:
        if dt.datetime.fromisoformat(s["posted_at"]) > day_ago:
            per_streamer[s["broadcaster"]] = per_streamer.get(s["broadcaster"], 0) + 1

    block = {s.lower() for s in cfg.get("streamers_blocklist") or []}
    langs = set(cfg.get("languages") or [])
    seen, out = set(), []
    for c in clips:
        login = c["broadcaster_name"].lower()
        if (c["id"] in posted_ids or c["id"] in seen or login in block
                or (langs and c.get("language") not in langs)
                or (games and c.get("game_id") not in games)
                or c["view_count"] < cfg["min_views"]
                or not cfg["min_duration"] <= c["duration"] <= cfg["max_duration"]
                or per_streamer.get(login, 0) >= cfg["max_per_streamer_per_day"]):
            continue
        seen.add(c["id"])
        c["_game_name"] = games.get(c.get("game_id"), "")
        out.append(c)
    out.sort(key=score, reverse=True)
    print(f"{len(clips)} Clips gefunden, {len(out)} passen zu den Filtern.")
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


def build_caption(cfg: dict, clip: dict, title: str) -> str:
    lang = clip.get("language", "en")
    tpl = cfg["caption_templates"].get(lang, cfg["caption_templates"]["en"])
    hooks = (cfg.get("hooks") or {}).get(lang) or (cfg.get("hooks") or {}).get("en") or [""]
    streamer = clip["broadcaster_name"]
    text = tpl.format(hook=random.choice(hooks), title=title, streamer=streamer,
                      streamer_tag=re.sub(r"[^a-z0-9_]", "", streamer.lower()),
                      game_tag=game_tag(clip.get("_game_name", "")))
    seen, parts = set(), []
    for word in text.split():  # doppelte Hashtags entfernen
        if word.startswith("#"):
            if word.lower() in seen or len(word) < 2:
                continue
            seen.add(word.lower())
        parts.append(word)
    return " ".join(parts).replace(" |  ", " | ").replace("  ", " ")[:2200]


def notify(cfg: dict, caption: str, cover_ms: int, clip: dict, mode: str) -> None:
    """Push aufs Handy (ntfy) + Zusammenfassung im GitHub-Lauf."""
    msg = (f"{caption}\n\n🖼 Titelbild: Sekunde {cover_ms / 1000:.1f}\n"
           f"🎥 Quelle: {clip['url']}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### {clip['broadcaster_name']} – {clip['view_count']} Aufrufe\n\n"
                    f"**Beschreibung (kopieren):**\n```\n{caption}\n```\n"
                    f"Titelbild: Sekunde {cover_ms / 1000:.1f} · Quelle: {clip['url']}\n\n")
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        title = ("Neuer Clip in deiner TikTok-Inbox – Beschreibung kopieren"
                 if mode == "inbox" else "Clip gepostet ✅")
        try:
            requests.post(f"https://ntfy.sh/{topic}", data=msg.encode("utf-8"), timeout=15,
                          headers={"Title": title.encode("utf-8"), "Tags": "movie_camera"})
        except requests.RequestException as e:
            print("ntfy fehlgeschlagen:", e)


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
    title = pick_title(clip, words)
    ass = video.make_ass(words, title, dur, src.with_suffix(".ass"))
    OUT.mkdir(exist_ok=True)
    mp4 = video.render(src, ass, OUT / f"{name}.mp4")
    cover_ms = video.find_cover_ms(src)
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

    state = load_state()
    candidates = find_candidates(cfg, state)
    if not candidates:
        print("Kein neuer passender Clip. Ende.")
        return

    token = None
    if not args.dry_run:
        from . import tiktok
        token, new_refresh = tiktok.refresh_access_token()
        if new_refresh != os.environ.get("TIKTOK_REFRESH_TOKEN"):
            print(f"::add-mask::{new_refresh}")
            (ROOT / ".new_refresh_token").write_text(new_refresh)

    done = 0
    for clip in candidates:
        if done >= cfg["clips_per_run"]:
            break
        print(f"\n▶ {clip['broadcaster_name']}: {clip['title']} "
              f"({clip['view_count']} Aufrufe, {clip['language']}) {clip['url']}")
        work = WORK / clip["id"]
        try:
            src = video.download(clip["url"], work)
            mp4, title, cover_ms = produce(cfg, src, clip, clip.get("language"), clip["id"])
            caption = build_caption(cfg, clip, title)
            print("Beschreibung:", caption)
            if not args.dry_run:
                from . import tiktok
                tiktok.upload(token, mp4, caption, cfg["tiktok_mode"], cfg["privacy_level"],
                              cover_ms)
            notify(cfg, caption, cover_ms, clip, "dry-run" if args.dry_run else cfg["tiktok_mode"])
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
    print(f"\n{done} Clip(s) verarbeitet.")


if __name__ == "__main__":
    main()
