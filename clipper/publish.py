"""Clip-Seite (GitHub Pages) aktualisieren + eine Push-Nachricht schicken.

    python -m clipper.publish site <ordner>   # neue Clips in den Seiten-Ordner übernehmen
    python -m clipper.publish notify          # warten bis die Seite live ist, dann ntfy-Push
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
KEEP = 24  # so viele Clips bleiben auf der Seite (ca. 1–2 Tage)


def _new_clips() -> list[dict]:
    f = OUT / "new_clips.json"
    return json.loads(f.read_text()) if f.exists() else []


def build_site(site: Path) -> None:
    clips_dir = site / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    index = site / "clips.json"
    old = json.loads(index.read_text()) if index.exists() else []
    new = _new_clips()
    for c in new:
        for name in (c["file"], c["thumb"]):
            if (OUT / name).exists():
                shutil.copy2(OUT / name, clips_dir / name)
    new_ids = {c["id"] for c in new}
    merged = new + [c for c in old if c["id"] not in new_ids]
    merged = merged[:KEEP]
    keep_files = {c["file"] for c in merged} | {c["thumb"] for c in merged}
    for f in clips_dir.iterdir():
        if f.name not in keep_files:
            f.unlink()
    index.write_text(json.dumps(merged, ensure_ascii=False, indent=1))
    print(f"Seite: {len(new)} neue Clips, {len(merged)} insgesamt.")


def _clean_topic(raw: str) -> str:
    t = raw.strip().strip("/")
    return t.rsplit("/", 1)[-1] if "/" in t else t


def _summary(text: str) -> None:
    print(text)
    s = os.environ.get("GITHUB_STEP_SUMMARY")
    if s:
        with open(s, "a", encoding="utf-8") as f:
            f.write(f"> {text}\n\n")


def notify() -> None:
    new = _new_clips()
    if not new:
        print("Keine neuen Clips – keine Nachricht.")
        return
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    owner, name = (repo.split("/", 1) + [""])[:2]
    page = f"https://{owner.lower()}.github.io/{name}/clips.html"

    # Warten, bis GitHub Pages die neuen Clips ausliefert (max. ~6 Minuten)
    ids = {c["id"] for c in new}
    for _ in range(36):
        try:
            r = requests.get(f"https://{owner.lower()}.github.io/{name}/clips.json",
                             params={"t": time.time()}, timeout=15)
            if r.ok and ids & {c.get("id") for c in r.json()}:
                break
        except Exception:
            pass
        time.sleep(10)
    else:
        print("Seite noch nicht aktualisiert – sende trotzdem.")

    topic = _clean_topic(os.environ.get("NTFY_TOPIC") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", topic or "-invalid-"):
        _summary("⚠️ **Keine Push-Nachricht:** Secret `NTFY_TOPIC` fehlt oder enthält ungültige Zeichen.")
        return
    test = "🧪 TEST – " if any(c.get("test") for c in new) else ""
    single_top = len(new) == 1 and str(new[0].get("id", "")).startswith("top")
    lines = [f"{i + 1}. {c['streamer']} – {c['views']:,} Aufrufe".replace(",", ".")
             for i, c in enumerate(new)]
    body = "\n".join(lines) + "\n\nAntippen → Clip-Seite öffnen → speichern & Beschreibung kopieren"
    title = (f"{test}🔥 Neues Top-Video bereit – jetzt posten!" if single_top
             else f"{test}🎬 {len(new)} neue Clips bereit")
    # Mehrere Varianten: falls ntfy eine ablehnt, geht die nächste, einfachere raus.
    variants = [
        {"title": title, "click": page, "tags": "movie_camera",
         "actions": f"view, Clips öffnen, {page}"},
        {"title": title, "click": page, "tags": "movie_camera"},
        {"title": title},
    ]
    errors = []
    for params in variants:
        data = body if "click" in params else body + "\n" + page
        try:
            r = requests.post(f"https://ntfy.sh/{topic}", data=data.encode("utf-8"),
                              params=params, timeout=30)
            if r.ok:
                _summary(f"✅ Push gesendet. Clip-Seite: {page}")
                if errors:
                    _summary("Hinweis – vorherige Versuche: " + "; ".join(errors))
                return
            errors.append(f"HTTP {r.status_code}: {r.text.strip()[:200]}".replace(topic, "***"))
        except Exception as e:
            errors.append(type(e).__name__)
    _summary("❌ **Push fehlgeschlagen:** " + "; ".join(errors))
    sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "site":
        build_site(Path(sys.argv[2]))
    elif len(sys.argv) >= 2 and sys.argv[1] == "notify":
        notify()
    else:
        print(__doc__)
        sys.exit(1)
