"""Einmalig: TikTok-Code aus der Callback-Seite gegen einen Refresh-Token tauschen."""
import os
from pathlib import Path

from . import tiktok

code = os.environ["TIKTOK_AUTH_CODE"].strip()
redirect = os.environ["TIKTOK_REDIRECT_URI"].strip()
data = tiktok.exchange_code(code, redirect)
print(f"::add-mask::{data['refresh_token']}")
print(f"::add-mask::{data['access_token']}")
Path(".new_refresh_token").write_text(data["refresh_token"])
print("Verbunden! Berechtigungen:", data.get("scope"))
