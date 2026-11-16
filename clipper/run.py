"""Hauptablauf: Clips finden -> herunterladen -> Untertitel -> 9:16 -> TikTok."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
from pathlib import Path

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


def build_caption(cfg: dict, clip: dict) -> str:
    lang = clip.get("language", "en")
    tpl = cfg["caption_templates"].get(lang, cfg["caption_templates"]["en"])
    return tpl.format(title=clip["title"].strip(), streamer=clip["broadcaster_name"],
                      game_tag=game_tag(clip.get("_game_name", "")))


def produce(cfg: dict, src: Path, title: str, language: str | None, name: str) -> Path:
    dur = video.probe_duration(src)
    words = []
    if cfg.get("captions"):
        try:
            words = video.transcribe(src, language, cfg.get("whisper_model", "small"))
            print(f"{len(words)} Wörter transkribiert.")
        except Exception as e:  # Untertitel sind nice-to-have, kein Abbruchgrund
            print("Untertitel übersprungen:", e)
    ass = video.make_ass(words, title, dur, src.with_suffix(".ass"))
    OUT.mkdir(exist_ok=True)
    return video.render(src, ass, OUT / f"{name}.mp4")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Nur Videos erstellen, nicht hochladen")
    ap.add_argument("--local-file", help="Test: lokale Datei statt Twitch-Clip schneiden")
    ap.add_argument("--title", default="Test Clip")
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())

    if args.local_file:
        out = produce(cfg, Path(args.local_file), args.title, None, "local_test")
        print("Fertig:", out)
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
            mp4 = produce(cfg, src, clip["title"], clip.get("language"), clip["id"])
            caption = build_caption(cfg, clip)
            print("Caption:", caption)
            if not args.dry_run:
                from . import tiktok
                tiktok.upload(token, mp4, caption, cfg["tiktok_mode"], cfg["privacy_level"])
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
