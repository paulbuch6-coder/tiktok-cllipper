"""Download, Untertitel und 9:16-Schnitt."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import yt_dlp

W, H = 1080, 1920


def download(url: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "outtmpl": str(out_dir / "source.%(ext)s"),
        "format": "best[ext=mp4]/best",
        "quiet": True,
        "noprogress": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return Path(ydl.prepare_filename(info))


def _ts(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


FONTS = Path(__file__).resolve().parent.parent / "fonts"
YELLOW = r"\c&H0000E5FF&"   # aktives Wort (BGR)
WHITE = r"\c&H00FFFFFF&"


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def clean_title(text: str) -> str:
    """Emojis/Sonderzeichen raus (Schrift hat keine), max. ~60 Zeichen, GROSS."""
    t = "".join(ch for ch in text if ord(ch) < 0x2000 or ch in "–—„“”‘’…")
    t = re.sub(r"\s+", " ", t).strip(" -|")
    if len(t) > 60:
        t = t[:60].rsplit(" ", 1)[0] + "…"
    return t.upper()


def make_ass(words: list[tuple[float, float, str]], title: str, duration: float,
             path: Path, credit: str = "") -> Path:
    """ASS-Datei im TikTok-Look:
    - Titel oben: schwarze Anton-Schrift auf weißem Balken (sofort lesbar, auch als Thumbnail)
    - Untertitel: Montserrat Black, weiß mit dickem Rand, aktives Wort gelb + kleiner Pop
    - Credit: twitch.tv/streamer klein unter dem Video
    """
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,Anton,92,&H00101010,&H00101010,&H00FFFFFF,&H00000000,0,0,0,0,100,100,1,0,3,22,0,8,90,90,250,1
Style: Cap,Montserrat Black,92,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,9,4,2,60,60,420,1
Style: Credit,Montserrat Black,38,&H40FFFFFF,&H40FFFFFF,&H80000000,&H00000000,0,0,0,0,100,100,2,0,1,3,0,2,60,60,555,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if title:
        lines.append(f"Dialogue: 0,{_ts(0)},{_ts(duration)},Title,,0,0,0,,"
                     rf"{{\fad(0,0)\t(0,120,\fscx104\fscy104)\t(120,240,\fscx100\fscy100)}}"
                     f"{_esc(clean_title(title))}")
    if credit:
        lines.append(f"Dialogue: 0,{_ts(0)},{_ts(duration)},Credit,,0,0,0,,{_esc(credit.upper())}")

    groups: list[list[tuple[float, float, str]]] = []
    cur: list[tuple[float, float, str]] = []
    for w in words:
        cur.append(w)
        if len(cur) >= 3 or w[2].rstrip().endswith((".", "!", "?", ",")):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)

    for gi, g in enumerate(groups):
        g_end = g[-1][1]
        if gi + 1 < len(groups):  # Lücken bis zur nächsten Gruppe kurz überbrücken
            g_end = min(max(g_end, groups[gi + 1][0][0] - 0.05), g_end + 0.4)
        for wi, (start, end, _) in enumerate(g):
            w_end = g[wi + 1][0] if wi + 1 < len(g) else g_end
            if w_end <= start:
                continue
            parts = []
            for wj, (_, _, txt) in enumerate(g):
                t = _esc(txt.upper().strip(".,"))
                if wj == wi:
                    parts.append(rf"{{{YELLOW}\fscx112\fscy112\t(0,90,\fscx100\fscy100)}}{t}{{{WHITE}}}")
                else:
                    parts.append(t)
            lines.append(f"Dialogue: 1,{_ts(start)},{_ts(w_end)},Cap,,0,0,0,,{' '.join(parts)}")

    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return path


def transcribe(src: Path, language: str | None, model_name: str) -> list[tuple[float, float, str]]:
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(src), language=language, word_timestamps=True,
                                   vad_filter=True)
    words = []
    for seg in segments:
        for w in seg.words or []:
            if w.word.strip():
                words.append((w.start, w.end, w.word.strip()))
    return words


def probe_duration(src: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(src)],
        capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


def find_cover_ms(src: Path) -> int:
    """Lautester Moment im Clip = meist der Höhepunkt -> wird zum Thumbnail."""
    dur = probe_duration(src)
    try:
        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(src), "-vn", "-af",
             "asetnsamples=n=4800,astats=metadata=1:reset=1,"
             "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
             "-f", "null", "-"],
            capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError:
        return int(min(1.0, dur / 2) * 1000)
    best_t, best_v, t = min(1.0, dur / 2), -1e9, None
    for line in out.splitlines():
        if line.startswith("frame:"):
            m = re.search(r"pts_time:([\d.]+)", line)
            t = float(m.group(1)) if m else None
        elif "RMS_level=" in line and t is not None:
            try:
                v = float(line.split("=", 1)[1])
            except ValueError:
                continue
            if 0.5 <= t <= dur - 0.5 and v > best_v:
                best_t, best_v = t, v
    return int(best_t * 1000)


def save_cover(video_file: Path, ms: int, out: Path) -> Path:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{ms / 1000:.3f}",
                    "-i", str(video_file), "-frames:v", "1", "-q:v", "2", str(out)], check=True)
    return out


def render(src: Path, ass: Path | None, out: Path) -> Path:
    """Hochformat: unscharfer, gezoomter Hintergrund + Originalvideo mittig + Untertitel."""
    vf = (
        f"[0:v]split=2[a][b];"
        f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
        f"boxblur=25:2,eq=brightness=-0.08[bg];"
        f"[b]scale={int(W * 1.15)}:-2,crop={W}:ih[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
    )
    if ass:
        vf += f",subtitles='{ass.as_posix()}':fontsdir='{FONTS.as_posix()}'"
    vf += "[v]"
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-i", str(src),
        "-filter_complex", vf, "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-maxrate", "1700k", "-bufsize", "3400k", "-pix_fmt", "yuv420p",
        "-r", "30", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out),
    ]
    subprocess.run(cmd, check=True)
    return out
