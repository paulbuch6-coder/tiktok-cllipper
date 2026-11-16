# TikTok Auto-Clipper (Twitch → TikTok)

Läuft komplett kostenlos auf **GitHub Actions**, ohne deinen PC. Alle 2 Stunden:

1. holt die Clips, die auf Twitch gerade viral gehen (GTA VI / GTA V, Deutsch + Englisch, sortiert nach Aufrufen pro Stunde)
2. lädt den besten neuen Clip herunter
3. erstellt automatisch Untertitel (Whisper, DE/EN)
4. schneidet ihn ins Hochformat 9:16 (unscharfer Hintergrund + Hook-Titel)
5. lädt ihn über die **offizielle TikTok-API** hoch und nennt den Streamer in der Beschreibung
6. merkt sich, was schon gepostet wurde (keine Duplikate, max. 2 Clips pro Streamer am Tag)

Alle Einstellungen stehen in [`config.yaml`](config.yaml) und lassen sich im Browser bearbeiten.

---

## Einrichtung (geht komplett am Handy, ca. 30 Minuten)

### 1. GitHub-Repo
- Erstelle ein Repo und lade diese Dateien hoch.
- **Öffentlich** = unbegrenzt viele kostenlose Actions-Minuten (die Secrets bleiben trotzdem geheim).
  **Privat** = 2000 Minuten im Monat. Dann in `.github/workflows/clipper.yml` den Takt auf `*/3` stellen.
- **Settings → Pages** → Source: *Deploy from a branch* → `main` / `/docs` → Save.
  Danach ist die Seite unter `https://DEINNAME.github.io/REPONAME/` erreichbar.

### 2. Twitch-App (kostenlos)
- https://dev.twitch.tv/console → *Register Your Application*
- Name: beliebig · OAuth Redirect URL: `http://localhost` · Kategorie: *Other* · Client-Typ: *Confidential*
- Notiere die **Client ID** und erstelle ein **Client Secret** („New Secret“).

### 3. TikTok-Developer-App (kostenlos)
- https://developers.tiktok.com → *Manage apps* → *Connect an app*
- Products hinzufügen: **Login Kit** und **Content Posting API**
- Scopes: `user.info.basic`, `video.upload`, `video.publish`
- Redirect URI (Login Kit, Web): `https://DEINNAME.github.io/REPONAME/callback.html`
- Terms of Service URL: `https://DEINNAME.github.io/REPONAME/#terms`
- Privacy Policy URL: `https://DEINNAME.github.io/REPONAME/#privacy`
- Notiere den **Client Key** und das **Client Secret**.
- **Sofort testen:** Wechsle oben auf *Sandbox* und füge deinen TikTok-Account als *Target User* hinzu.
- **Für öffentliches Posten:** App zur Prüfung einreichen („Submit for review“). Das dauert ein paar Tage.

### 4. GitHub-Token, damit sich der Clipper den TikTok-Login merken kann
- GitHub → Settings → Developer settings → *Fine-grained tokens* → *Generate new token*
- Repository access: nur dieses Repo · Permissions: **Secrets → Read and write**
- Laufzeit: 1 Jahr

### 5. Secrets eintragen
Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Name | Wert |
|---|---|
| `TWITCH_CLIENT_ID` | aus Schritt 2 |
| `TWITCH_CLIENT_SECRET` | aus Schritt 2 |
| `TIKTOK_CLIENT_KEY` | aus Schritt 3 |
| `TIKTOK_CLIENT_SECRET` | aus Schritt 3 |
| `GH_PAT` | aus Schritt 4 |

### 6. TikTok verbinden (einmalig)
Öffne diesen Link am Handy. Vorher `CLIENT_KEY`, `DEINNAME` und `REPONAME` ersetzen:

```
https://www.tiktok.com/v2/auth/authorize/?client_key=CLIENT_KEY&scope=user.info.basic,video.upload,video.publish&response_type=code&state=clipper&redirect_uri=https%3A%2F%2FDEINNAME.github.io%2FREPONAME%2Fcallback.html
```

→ Erlauben → Code kopieren → Repo → **Actions → „TikTok verbinden (einmalig)“ → Run workflow** → Code einfügen.
Mach das zügig, denn der Code läuft nach wenigen Minuten ab.

### 7. Testen
- **Actions → „TikTok Clipper“ → Run workflow → „dry_run“ anhaken.** Der Lauf erstellt nur das Video.
  Du findest es unten im Lauf unter *Artifacts*.
- Passt alles: noch einmal ohne Haken starten. Der Clip landet als Entwurf in deiner TikTok-App.
- Ab jetzt läuft alles automatisch alle 2 Stunden.

---

## Modi (`config.yaml`)

| Einstellung | Bedeutung |
|---|---|
| `tiktok_mode: "inbox"` | **Start-Empfehlung.** Der Clip kommt als Entwurf in deine TikTok-Benachrichtigungen, du tippst nur auf „Posten“. |
| `tiktok_mode: "direct"` + `privacy_level: "PUBLIC_TO_EVERYONE"` | **Vollautomatisch.** Funktioniert erst, wenn TikTok deine App geprüft hat. |

## Wichtig
- **Nur Streamer clippen, die das erlauben.** Trag sie in `streamers_allowlist` ein oder sperr sie über `streamers_blocklist`.
- Twitch-Clips laufen schon nach kurzer Zeit gut. Für mehr Reichweite lieber 6–12 gute Clips am Tag als Spam.
- Läuft es mal nicht, findest du unter **Actions** bei jedem Lauf das Log mit dem Fehler.
