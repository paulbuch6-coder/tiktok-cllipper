"""Entscheidet, ob jetzt ein Lauf fällig ist (für den 20-Minuten-Zeitplan).

Gibt run=true/false für GitHub Actions aus. Ein Lauf ist fällig, wenn die letzte
Postzeit (config: post_hours, deutsche Zeit) erreicht ist und seitdem noch kein Lauf war.
"""
import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
LAST = ROOT / "state" / "last_run.json"
TZ = ZoneInfo("Europe/Berlin")


def last_run() -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(json.loads(LAST.read_text())["at"])
    except Exception:
        return None


def mark_run() -> None:
    LAST.parent.mkdir(exist_ok=True)
    LAST.write_text(json.dumps({"at": dt.datetime.now(dt.timezone.utc).isoformat()}))


def due(now: dt.datetime | None = None) -> tuple[bool, str]:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    hours = sorted(int(h) for h in cfg.get("post_hours", [11, 13, 15, 17, 19, 21]))
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(TZ)
    slots = [now.replace(hour=h, minute=0, second=0, microsecond=0) for h in hours]
    past = [s for s in slots if s <= now]
    if not past:
        return False, f"{now:%H:%M} – noch kein Postzeitpunkt heute"
    slot = past[-1]
    if now - slot > dt.timedelta(minutes=110):
        return False, f"{now:%H:%M} – Zeitpunkt {slot:%H:%M} zu lange her, warte auf den nächsten"
    last = last_run()
    if last and last.astimezone(TZ) >= slot - dt.timedelta(minutes=15):
        return False, f"{now:%H:%M} – Zeitpunkt {slot:%H:%M} schon erledigt"
    if last and now - last.astimezone(TZ) < dt.timedelta(minutes=75):
        return False, f"{now:%H:%M} – letzter Lauf erst vor Kurzem"
    return True, f"{now:%H:%M} – Zeitpunkt {slot:%H:%M} ist fällig"


if __name__ == "__main__":
    ok, why = due()
    print(why)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"run={'true' if ok else 'false'}\n")
