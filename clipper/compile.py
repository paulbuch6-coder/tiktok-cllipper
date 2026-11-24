"""Top-5-Format: die besten Momente als EIN Video mit Countdown (#5 -> #1).

Aufbau:  Teaser (Ausschnitt von #1, "WARTET AUF #1")  ->  #5  ->  #4  ->  #3  ->  #2  ->  #1
Jeder Moment: Split-Layout (Facecam oben, Gameplay unten), Untertitel,
Ranking + Streamer als Titel, Credit twitch.tv/name.
"""
from __future__ import annotations

import datetime as dt
import json
import random
import re
import shutil
import subprocess
from pathlib import Path

from . import video
from .run import OUT, WORK, plan_cut, save_state, write_summary

W, H = video.W, video.H


def _has_audio(f: Path) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                          "stream=index", "-of", "csv=p=0", str(f)],
                         capture_output=True, text=True).stdout.strip()
    return bool(out)


def _ensure_audio(f: Path) -> Path:
    """concat braucht überall eine Tonspur – notfalls Stille hinzufügen."""
    if _has_audio(f):
        return f
    fixed = f.with_name(f.stem + "_a.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(f), "-f", "lavfi", "-i",
                    "anullsrc=r=48000:cl=stereo", "-shortest", "-c:v", "copy", "-c:a", "aac",
                    str(fixed)], check=True)
    return fixed


def _segment(cfg: dict, src: Path, words: list, loud_ms: int, start: float, length: float,
             title: str, credit: str, out: Path, captions: bool = True) -> Path:
    seg_words = [(a - start, e - start, w) for a, e, w in words
                 if e > start and a < start + length]
    seg_words = [(max(a, 0.0), min(e, length), w) for a, e, w in seg_words] if captions else []
    facecam = None
    if cfg.get("split_layout", True):
        try:
            facecam = video.detect_facecam(src, start, length)
        except Exception as e:
            print("Facecam-Erkennung übersprungen:", e)
    if not cfg.get("credit_overlay"):
        credit = ""  # sieht für TikTok wie ein Wasserzeichen aus -> Credit nur in der Beschreibung
    ass = video.make_ass(seg_words, title, length, out.with_suffix(".ass"), credit=credit,
                         split=bool(facecam))
    video.render(src, ass, out, start=start, length=length, facecam=facecam)
    return _ensure_audio(out)


def _concat(parts: list[Path], out: Path) -> Path:
    inputs = []
    for p in parts:
        inputs += ["-i", str(p)]
    n = len(parts)
    streams = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(n))
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex",
                    f"{streams}concat=n={n}:v=1:a=1[v][a]", "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                    "-maxrate", "2500k", "-bufsize", "5000k", "-pix_fmt", "yuv420p", "-r", "30",
                    "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart",
                    str(out)], check=True)
    return out


def _pick(candidates: list[dict], n: int, per_streamer: int) -> list[dict]:
    chosen, count = [], {}
    for c in candidates:
        s = c["broadcaster_name"].lower()
        if count.get(s, 0) >= per_streamer:
            continue
        chosen.append(c)
        count[s] = count.get(s, 0) + 1
        if len(chosen) >= n:
            break
    return chosen


def build_caption(cfg: dict, moments: list[dict]) -> str:
    cc = cfg.get("compilation") or {}
    streamers = list(dict.fromkeys(m["broadcaster_name"] for m in moments))
    game = cc.get("game_keyword", "GTA 6")
    first = random.choice(cc.get("caption_openers") or ["Die 5 krassesten {game} Momente von heute 😳"])
    cta = random.choice(cc.get("caption_ctas") or ["Welcher war euer Favorit? 👇"])
    who = ", ".join(streamers[:-1]) + (f" & {streamers[-1]}" if len(streamers) > 1 else streamers[0])
    tags = list(cc.get("hashtags_core") or ["#gta6"])
    pool = list(cc.get("hashtags_pool") or [])
    random.shuffle(pool)
    for s in streamers[:2]:
        tags.append("#" + re.sub(r"[^a-z0-9_]", "", s.lower()))
    for t in pool:
        if len(tags) >= int(cc.get("max_hashtags", 6)):
            break
        if t not in tags:
            tags.append(t)
    return "\n".join([
        first.format(game=game),
        cta,
        f"Mit {who}",
        f"{game} deutsch – beste Twitch Momente",
        " ".join(tags),
    ])


def run_compilation(cfg: dict, args, state: list[dict], candidates: list[dict], mode: str) -> None:
    cc = cfg.get("compilation") or {}
    n = int(cc.get("moments", 5))
    seg_max = float(cc.get("moment_max_seconds", 15))
    teaser_len = float(cc.get("teaser_seconds", 2.0))
    picked = _pick(candidates, n + 3, int(cc.get("max_per_streamer", 2)))  # Reserve, falls einer scheitert
    if len(picked) < int(cc.get("min_moments", 3)):
        print(f"Nur {len(picked)} passende Momente – zu wenig für ein Top-Video. Ende.")
        return

    seg_cfg = dict(cfg, max_output_seconds=seg_max)
    ready = []  # (clip, src, words, loud_ms, start, length)
    for clip in picked:
        if len(ready) >= n:
            break
        print(f"\n▶ {clip['broadcaster_name']}: {clip['title']} ({clip['view_count']} Aufrufe) {clip['url']}")
        work = WORK / clip["id"]
        try:
            src = video.download(clip["url"], work)
            if cfg.get("nsfw_filter", True):
                bad, why = video.nsfw_check(src, strict=cfg.get("nsfw_strict", True))
                if bad:
                    print("⛔ Übersprungen (Nacktheit erkannt):", why)
                    continue
            dur = video.probe_duration(src)
            words = []
            if cfg.get("captions"):
                try:
                    words = video.transcribe(src, clip.get("language"), cfg.get("whisper_model", "small"))
                except Exception as e:
                    print("Untertitel übersprungen:", e)
            loud_ms = video.find_cover_ms(src)
            start, length = plan_cut(seg_cfg, dur, words, loud_ms / 1000)
            ready.append((clip, src, words, loud_ms, start, length))
        except Exception as e:
            print("Moment übersprungen:", e)
    if len(ready) < int(cc.get("min_moments", 3)):
        print("Zu wenige Momente erfolgreich geladen. Ende.")
        return

    # Ranking: stärkster Moment (Platz 1 im Kandidaten-Ranking) wird #1 und kommt zuletzt
    total = len(ready)
    OUT.mkdir(exist_ok=True)
    tmp = WORK / "compilation"
    tmp.mkdir(parents=True, exist_ok=True)
    parts = []

    # Teaser: Ausschnitt aus #1 rund um den Höhepunkt
    c1, src1, words1, loud1, _, _ = ready[0]
    t_start = max(loud1 / 1000 - teaser_len * 0.6, 0)
    teaser_title = cc.get("teaser_title", "TOP {n} {game} MOMENTE HEUTE – WARTET AUF #1").format(
        n=total, game=cc.get("game_keyword", "GTA 6").upper())
    parts.append(_segment(cfg, src1, words1, loud1, t_start, teaser_len, teaser_title,
                          "", tmp / "00_teaser.mp4", captions=False))

    for rank in range(total, 0, -1):
        clip, src, words, loud_ms, start, length = ready[rank - 1]
        title = f"#{rank} · {clip['broadcaster_name'].upper()}"
        parts.append(_segment(cfg, src, words, loud_ms, start, length, title,
                              f"twitch.tv/{clip['broadcaster_name']}",
                              tmp / f"{10 - rank:02d}_rank{rank}.mp4"))

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M")
    vid_id = f"top{total}_{stamp}"
    final = _concat(parts, OUT / f"{vid_id}.mp4")
    cover_ms = int(teaser_len * 0.6 * 1000)
    video.save_cover(final, cover_ms, OUT / f"{vid_id}_thumbnail.jpg")
    secs = video.probe_duration(final)
    caption = build_caption(cfg, [r[0] for r in ready])
    print(f"\nFertig: {final.name} ({secs:.0f} s)\n{caption}")

    summary_clip = {"broadcaster_name": f"Top {total}", "view_count": sum(r[0]["view_count"] for r in ready),
                    "url": ", ".join(r[0]["url"] for r in ready), "_option": ""}
    write_summary(summary_clip, caption, cover_ms)
    new_clips = [{
        "id": vid_id, "file": final.name, "thumb": f"{vid_id}_thumbnail.jpg",
        "streamer": "Top " + str(total) + ": " + ", ".join(dict.fromkeys(r[0]["broadcaster_name"] for r in ready)),
        "views": summary_clip["view_count"], "game": cc.get("game_keyword", "GTA 6"),
        "caption": caption, "cover_s": round(cover_ms / 1000, 1), "option": 1,
        "source": ready[0][0]["url"], "test": mode == "dry-run",
        "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}]
    (OUT / "new_clips.json").write_text(json.dumps(new_clips, ensure_ascii=False, indent=2))

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for clip, *_ in ready:
        state.append({"id": clip["id"], "broadcaster": clip["broadcaster_name"].lower(),
                      "posted_at": now, "url": clip["url"], "dry_run": args.dry_run})
    if not args.dry_run:
        save_state(state)
    shutil.rmtree(WORK, ignore_errors=True)
