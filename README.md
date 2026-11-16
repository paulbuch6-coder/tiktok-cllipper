# TikTok Auto-Clipper (Twitch → Handy → TikTok)

Läuft kostenlos auf **GitHub Actions**, ohne deinen PC. Alle 2 Stunden:

1. holt die Clips, die auf Twitch gerade viral gehen (GTA VI / GTA V, Deutsch + Englisch)
2. lädt den besten neuen Clip herunter
3. erstellt automatisch Untertitel (Whisper, DE/EN)
4. schneidet ihn ins Hochformat 9:16 (unscharfer Hintergrund + Hook-Titel)
5. schreibt die Beschreibung (Hook + Titel + Streamer + Hashtags) und sucht das Thumbnail aus
6. schickt dir **Video + Beschreibung per Push aufs Handy** (App *ntfy*)

Du lädst das Video herunter, öffnest TikTok, lädst es hoch, fügst die Beschreibung ein und postest.
Pro Clip dauert das etwa 30 Sekunden.

Alle Einstellungen stehen in [`config.yaml`](config.yaml) und lassen sich im Browser bearbeiten (Stift-Symbol).

---

## Einrichtung

### 1. Twitch-App ✅ (erledigt)
Die Secrets `TWITCH_CLIENT_ID` und `TWITCH_CLIENT_SECRET` sind eingetragen.

### 2. Push-Nachrichten aufs Handy (2 Minuten)
1. Installiere die kostenlose App **ntfy** (Play Store / App Store).
2. Tippe auf **„+“** und abonniere einen **langen, zufälligen Namen**, z. B. `clipper-x7k29qpl4m`.
   Den Namen nicht weitergeben, denn wer ihn kennt, kann mitlesen.
3. Trag auf GitHub unter **Settings → Secrets and variables → Actions → New repository secret** ein:
   Name `NTFY_TOPIC`, Wert = derselbe Name wie in der App.

### 3. Testlauf
1. **Actions → „TikTok Clipper“ → Run workflow → Run workflow** (ohne Haken).
2. Nach 3–5 Minuten kommen zwei Push-Nachrichten:
   - **🎬 Neuer Clip:** antippen und das Video herunterladen
   - **📋 Beschreibung:** lange drücken und kopieren
3. In TikTok: **„+“** → Video hochladen → Beschreibung einfügen → als Titelbild die angegebene Sekunde wählen → Posten.

Ab dann läuft alles automatisch alle 2 Stunden.

---

## Häufige Einstellungen (`config.yaml`)

| Was | Einstellung |
|---|---|
| Nur bestimmte Streamer | `streamers_allowlist: ["name1", "name2"]` |
| Streamer ausschließen | `streamers_blocklist: ["name"]` |
| Mehr/weniger viral | `min_views: 300` |
| Hooks & Hashtags | `hooks:` und `caption_templates:` |
| Takt ändern | `.github/workflows/clipper.yml` → `cron: "17 */2 * * *"` (`*/3` = alle 3 Stunden) |
| Pausieren | Actions → TikTok Clipper → **„…“ → Disable workflow** |

## Später: über die TikTok-API hochladen (optional)
Der Code kann Clips auch direkt als Entwurf in deine TikTok-App schicken (`tiktok_mode: "inbox"`) oder sie posten (`"direct"`).
Dafür brauchst du eine von TikTok geprüfte Developer-App: Login Kit + Content Posting API, Redirect URI
`https://paulbuch6-coder.github.io/tiktok-cllipper/callback.html`, dazu die Secrets `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `GH_PAT`
und einmal den Workflow „TikTok verbinden“ ausführen. Die URL-Verifizierung für GitHub Pages liegt schon in `docs/`.

## Wichtig
- **Nur Streamer clippen, die das erlauben.** Sonst drohen Copyright-Strikes auf deinem Account.
- Wenn ein Lauf ein rotes X zeigt: Lauf antippen → Log ansehen bzw. als Screenshot weitergeben.
