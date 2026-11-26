"""Deutscher Sprecher fürs Top-5-Format (kostenlos, läuft lokal mit Piper TTS).

Vor jedem Moment sagt die Stimme z. B. "Platz drei: Trymacs. Und dann eskaliert alles."
Der Originalton wird währenddessen leiser gedreht (Ducking).
"""
from __future__ import annotations

import random
import re
import subprocess
import sys
import tarfile
from pathlib import Path

import requests

CACHE = Path.home() / ".cache" / "piper-voices"
NUM = {1: "eins", 2: "zwei", 3: "drei", 4: "vier", 5: "fünf", 6: "sechs", 7: "sieben"}
SOURCES = [
    # bessere Stimme (Hugging Face)
    ("de_DE-thorsten-high",
     "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/de/de_DE/thorsten/high/de_DE-thorsten-high.onnx"),
    # Fallback (GitHub)
    ("de-thorsten-low",
     "https://github.com/rhasspy/piper/releases/download/v0.0.2/voice-de-thorsten-low.tar.gz"),
]


def ensure_voice() -> Path | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    for name, url in SOURCES:
        model = CACHE / f"{name}.onnx"
        if model.exists() and (CACHE / f"{name}.onnx.json").exists():
            return model
        try:
            if url.endswith(".tar.gz"):
                tgz = CACHE / "voice.tgz"
                tgz.write_bytes(requests.get(url, timeout=120).content)
                with tarfile.open(tgz) as t:
                    for m in t.getmembers():
                        if m.name.endswith((".onnx", ".onnx.json")):
                            m.name = Path(m.name).name
                            t.extract(m, CACHE)
                tgz.unlink()
            else:
                for suffix in (".onnx", ".onnx.json"):
                    r = requests.get(url.replace(".onnx", suffix), timeout=180)
                    r.raise_for_status()
                    (CACHE / f"{name}{suffix}").write_bytes(r.content)
            if model.exists():
                print("Stimme bereit:", name)
                return model
        except Exception as e:
            print(f"Stimme {name} nicht ladbar: {e}")
    return None


def tts(text: str, out: Path, speed: float = 0.92) -> Path | None:
    model = ensure_voice()
    if not model:
        return None
    subprocess.run([sys.executable, "-m", "piper", "-m", str(model),
                    "-c", str(model) + ".json", "-f", str(out),
                    "--length-scale", str(speed)],
                   input=text.encode("utf-8"), check=True, capture_output=True)
    return out if out.exists() else None


def duration(wav: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(wav)], capture_output=True, text=True).stdout
    return float(out.strip() or 0)


def spoken_name(cfg: dict, login: str) -> str:
    names = {k.lower(): v for k, v in ((cfg.get("compilation") or {}).get("spoken_names") or {}).items()}
    if login.lower() in names:
        return names[login.lower()]
    n = re.sub(r"\d+$", "", login).replace("_", " ").strip()
    return n[:1].upper() + n[1:] if n else login


def _clean(text: str) -> str:
    t = re.sub(r"https?://\S+|#\S+|@\S+", "", text)
    t = "".join(ch for ch in t if ch.isalnum() or ch in " .,!?'-äöüÄÖÜß")
    t = re.sub(r"\s+", " ", t).strip(" .-")
    words = t.split()
    return " ".join(words[:12])


def intro_line(cfg: dict, n: int) -> str:
    v = (cfg.get("compilation") or {}).get("voice") or {}
    lines = v.get("intro") or ["Die {n} krassesten GTA 6 Momente von heute. Bleibt bis Platz eins!"]
    game = (cfg.get("compilation") or {}).get("game_keyword", "GTA 6")
    return random.choice(lines).format(n=NUM.get(n, n), game=game)


def rank_line(cfg: dict, rank: int, clip: dict, informative_title: str = "") -> str:
    v = (cfg.get("compilation") or {}).get("voice") or {}
    name = spoken_name(cfg, clip["broadcaster_name"])
    ctx = _clean(informative_title) if informative_title else ""
    if not ctx:
        ctx = random.choice(v.get("contexts") or ["Achtet mal auf seine Reaktion."])
    tpl_key = "rank_one" if rank == 1 else "rank"
    tpls = v.get(tpl_key) or (["Und Platz eins: {name}. {context}"] if rank == 1
                              else ["Platz {rank}: {name}. {context}"])
    line = random.choice(tpls).format(rank=NUM.get(rank, rank), name=name, context=ctx)
    return line if line.rstrip().endswith((".", "!", "?")) else line + "."


def mix(video: Path, voice_wav: Path, out: Path, delay: float = 0.15,
        duck: float = 0.25, gain: float = 1.4) -> Path:
    """Sprecher über den Clip legen, Originalton währenddessen leiser."""
    vd = duration(voice_wav)
    end = delay + vd + 0.2
    d = int(delay * 1000)
    fc = (f"[0:a]volume='if(between(t,0,{end:.2f}),{duck},1)':eval=frame[a0];"
          f"[1:a]adelay={d}|{d},volume={gain},apad[v];"
          f"[a0][v]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.9[a]")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-i", str(voice_wav),
                    "-filter_complex", fc, "-map", "0:v", "-map", "[a]", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "160k", "-ar", "48000", str(out)], check=True)
    return out
