"""TikTok Content Posting API (offiziell, kostenlos).

Modus "inbox":  Video landet als Entwurf in der TikTok-App (Scope video.upload).
Modus "direct": Video wird direkt gepostet (Scope video.publish). Vor der
                App-Prüfung durch TikTok nur mit privacy_level SELF_ONLY möglich.
"""
from __future__ import annotations

import math
import os
import time
from pathlib import Path

import requests

API = "https://open.tiktokapis.com/v2"
CHUNK = 10 * 1024 * 1024  # 10 MB


def refresh_access_token() -> tuple[str, str]:
    """Gibt (access_token, refresh_token) zurück. Der Refresh-Token kann sich ändern."""
    r = requests.post(
        f"{API}/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": os.environ["TIKTOK_CLIENT_KEY"],
            "client_secret": os.environ["TIKTOK_CLIENT_SECRET"],
            "grant_type": "refresh_token",
            "refresh_token": os.environ["TIKTOK_REFRESH_TOKEN"],
        },
        timeout=30,
    )
    data = r.json()
    if "access_token" not in data:
        raise RuntimeError(f"TikTok-Token-Refresh fehlgeschlagen: {data}")
    return data["access_token"], data.get("refresh_token", os.environ["TIKTOK_REFRESH_TOKEN"])


def exchange_code(code: str, redirect_uri: str) -> dict:
    r = requests.post(
        f"{API}/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": os.environ["TIKTOK_CLIENT_KEY"],
            "client_secret": os.environ["TIKTOK_CLIENT_SECRET"],
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    data = r.json()
    if "refresh_token" not in data:
        raise RuntimeError(f"Code-Tausch fehlgeschlagen: {data}")
    return data


def _post(token: str, path: str, body: dict) -> dict:
    r = requests.post(
        f"{API}/{path}", json=body, timeout=60,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=UTF-8"},
    )
    data = r.json()
    err = data.get("error", {})
    if err.get("code") not in (None, "ok"):
        raise RuntimeError(f"TikTok API {path}: {err}")
    return data.get("data", {})


def upload(token: str, video: Path, caption: str, mode: str, privacy: str) -> str:
    size = video.stat().st_size
    chunk = size if size <= CHUNK else CHUNK
    count = max(1, size // chunk)  # letzter Chunk darf größer sein (TikTok-Regel)
    source = {"source": "FILE_UPLOAD", "video_size": size,
              "chunk_size": chunk, "total_chunk_count": count}

    if mode == "direct":
        info = _post(token, "post/publish/creator_info/query/", {})
        allowed = info.get("privacy_level_options", [])
        if privacy not in allowed:
            print(f"privacy_level {privacy} nicht erlaubt ({allowed}) -> nutze {allowed[0]}")
            privacy = allowed[0]
        init = _post(token, "post/publish/video/init/", {
            "post_info": {"title": caption[:2200], "privacy_level": privacy,
                          "disable_duet": False, "disable_comment": False,
                          "disable_stitch": False},
            "source_info": source,
        })
    else:
        init = _post(token, "post/publish/inbox/video/init/", {"source_info": source})

    publish_id, url = init["publish_id"], init["upload_url"]
    with open(video, "rb") as f:
        for i in range(count):
            start = i * chunk
            length = (size - start) if i == count - 1 else chunk
            data = f.read(length)
            r = requests.put(url, data=data, timeout=300, headers={
                "Content-Type": "video/mp4",
                "Content-Length": str(len(data)),
                "Content-Range": f"bytes {start}-{start + len(data) - 1}/{size}",
            })
            if r.status_code not in (200, 201, 206):
                raise RuntimeError(f"Upload-Chunk {i} fehlgeschlagen: {r.status_code} {r.text}")

    for _ in range(30):
        st = _post(token, "post/publish/status/fetch/", {"publish_id": publish_id})
        status = st.get("status")
        print("TikTok-Status:", status)
        if status in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
            return publish_id
        if status == "FAILED":
            raise RuntimeError(f"TikTok meldet FAILED: {st}")
        time.sleep(10)
    return publish_id
