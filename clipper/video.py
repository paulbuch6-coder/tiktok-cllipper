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
             path: Path, credit: str = "", split: bool = False) -> Path:
    """ASS-Datei im TikTok-Look:
    - Titel oben: schwarze Anton-Schrift auf weißem Balken (sofort lesbar, auch als Thumbnail)
    - Untertitel: Montserrat Black, weiß mit dickem Rand, aktives Wort gelb + kleiner Pop
    - Credit: twitch.tv/streamer klein unter dem Video
    """
    # Split: Titel auf der Naht zwischen Facecam und Gameplay, Untertitel im unteren Drittel
    t_mv, c_mv, r_mv = (TOP_H - 75, 360, 50) if split else (190, 400, 520)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,Anton,92,&H00101010,&H00101010,&H00FFFFFF,&H00000000,0,0,0,0,100,100,1,0,3,22,0,8,90,90,{t_mv},1
Style: Cap,Montserrat Black,92,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,9,4,2,60,60,{c_mv},1
Style: Credit,Montserrat Black,38,&H40FFFFFF,&H40FFFFFF,&H80000000,&H00000000,0,0,0,0,100,100,2,0,1,3,0,2,60,60,{r_mv},1

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


MODEL = Path(__file__).resolve().parent.parent / "models" / "face_detection_yunet_2023mar.onnx"
TOP_H = 700            # Höhe der Facecam oben im Split-Layout
BOT_H = H - TOP_H      # Gameplay unten


def detect_facecam(src: Path, start: float = 0.0, length: float | None = None,
                   samples: int = 12) -> tuple[int, int, int, int] | None:
    """Sucht die Facecam des Streamers: ein Gesicht, das in vielen Frames an
    (fast) derselben Stelle ist. Gibt den Ausschnitt (x, y, w, h) im Original
    zurück – passend zum Seitenverhältnis des oberen Bereichs – oder None."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    if not MODEL.exists():
        return None
    dur = length or (probe_duration(src) - start)
    sw, sh = 960, 540
    det = cv2.FaceDetectorYN.create(str(MODEL), "", (sw, sh), 0.7, 0.3, 50)
    faces_per_frame = []
    for i in range(samples):
        t = start + dur * (i + 0.5) / samples
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
             "-vf", f"scale={sw}:{sh}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
            capture_output=True).stdout
        if len(raw) != sw * sh * 3:
            continue
        img = np.frombuffer(raw, np.uint8).reshape(sh, sw, 3)
        _, faces = det.detect(img)
        faces_per_frame.append([] if faces is None else [f[:4] for f in faces])
    n = len(faces_per_frame)
    if n < 4:
        return None
    # Kandidaten clustern: Gesichter an fast gleicher Position in vielen Frames
    cands = [f for fr in faces_per_frame for f in fr]
    best, best_hits = None, 0
    for c in cands:
        cx, cy = c[0] + c[2] / 2, c[1] + c[3] / 2
        hits, boxes = 0, []
        for fr in faces_per_frame:
            near = [f for f in fr if abs(f[0] + f[2] / 2 - cx) < sw * 0.04
                    and abs(f[1] + f[3] / 2 - cy) < sh * 0.06]
            if near:
                hits += 1
                boxes.append(near[0])
        if hits > best_hits:
            best, best_hits = boxes, hits
    if not best or best_hits < max(4, int(n * 0.45)):
        return None
    fx, fy, fw, fh = (float(np.median([b[k] for b in best])) for k in range(4))
    cx, cy = fx + fw / 2, fy + fh / 2
    # Facecams sitzen fast immer am Rand; Gesichter mitten im Bild sind meist Spielfiguren
    if 0.3 * sw < cx < 0.7 * sw and 0.25 * sh < cy < 0.75 * sh:
        return None
    if fw > sw * 0.35:  # riesiges Gesicht = Vollbild-Kamera, kein Overlay
        return None
    # Ausschnitt um das Gesicht, im Seitenverhältnis des oberen Bereichs
    aspect = W / TOP_H
    cw = min(fw * 3.4, sw)
    ch = cw / aspect
    x = min(max(cx - cw / 2, 0), sw - cw)
    y = min(max(cy - ch * 0.45, 0), sh - ch)
    # zurück auf Originalauflösung
    ow, oh = (int(v) for v in subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", str(src)],
        capture_output=True, text=True).stdout.strip().split(",")[:2])
    fxs, fys = ow / sw, oh / sh
    box = (int(x * fxs) // 2 * 2, int(y * fys) // 2 * 2,
           int(cw * fxs) // 2 * 2, int(ch * fys) // 2 * 2)
    print(f"Facecam erkannt in {best_hits}/{n} Frames: {box}")
    return box


def render(src: Path, ass: Path | None, out: Path, start: float = 0.0,
           length: float | None = None,
           facecam: tuple[int, int, int, int] | None = None) -> Path:
    """Hochformat.
    Mit Facecam: Facecam groß oben, Gameplay bildschirmfüllend unten (Split-Layout).
    Ohne: unscharfer Hintergrund + vergrößertes Video mittig."""
    if facecam:
        x, y, w, h = facecam
        vf = (
            f"[0:v]split=2[a][b];"
            f"[a]crop={w}:{h}:{x}:{y},scale={W}:{TOP_H},setsar=1[top];"
            f"[b]crop=trunc(ih*{W}/{BOT_H}/2)*2:ih:(iw-ow)/2:0,scale={W}:{BOT_H},setsar=1[bot];"
            f"[top][bot]vstack=inputs=2"
        )
    else:
        vf = (
            f"[0:v]split=2[a][b];"
            f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"boxblur=25:2,eq=brightness=-0.08[bg];"
            f"[b]scale={int(W * 1.3)}:-2,crop={W}:ih[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    if ass:
        vf += f",subtitles='{ass.as_posix()}':fontsdir='{FONTS.as_posix()}'"
    vf += "[v]"
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}",
        *(["-t", f"{length:.3f}"] if length else []), "-i", str(src),
        "-filter_complex", vf, "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-maxrate", "1700k", "-bufsize", "3400k", "-pix_fmt", "yuv420p",
        "-r", "30", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out),
    ]
    subprocess.run(cmd, check=True)
    return out

NSFW_CLASSES = {"FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED",
                "BUTTOCKS_EXPOSED", "ANUS_EXPOSED"}
_nude_detector = None


def nsfw_check(src: Path, samples: int = 16, threshold: float = 0.45) -> tuple[bool, str]:
    """Prüft Stichproben-Frames auf Nacktheit (auch im Spiel – TikTok unterscheidet nicht).
    Gibt (problematisch?, Begründung) zurück."""
    global _nude_detector
    try:
        from nudenet import NudeDetector
    except ImportError:
        return False, "NudeNet nicht installiert"
    if _nude_detector is None:
        _nude_detector = NudeDetector()
    dur = probe_duration(src)
    tmp = src.parent / "_nsfw"
    tmp.mkdir(exist_ok=True)
    try:
        for i in range(samples):
            t = dur * (i + 0.5) / samples
            f = tmp / f"{i}.jpg"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(src),
                            "-frames:v", "1", "-vf", "scale=960:-2", str(f)], check=False)
            if not f.exists():
                continue
            for d in _nude_detector.detect(str(f)):
                if d.get("class") in NSFW_CLASSES and d.get("score", 0) >= threshold:
                    return True, f"{d['class']} ({d['score']:.2f}) bei {t:.1f}s"
    finally:
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)
    return False, "ok"
