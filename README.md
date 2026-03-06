# Spotify Playlist SuperSorter

A local Streamlit web app to analyze and filter Spotify playlists with enriched genre and audio metadata.

## Features
- Spotify OAuth login (local callback, supports ngrok HTTPS redirect).
- Load all playlists (including private/collaborative read scope).
- Includes `Liked Songs` as a virtual playlist source.
- Genre attribution is artist-based (Spotify limitation) and uses the main artist on each track.
- Filters:
  - Genre
  - Artist
  - Release year
  - Audio features (`danceability`, `energy`, `valence`, `tempo`, etc.)
- Global `AND / OR` filter mode.
- Relevance scoring + match reasons.
- Fixed-size top-10 genre distribution chart for the selected source (count + share, labels like `12 (34%)`).
- Inline Spotify track embed player driven by row selection in the results table.
- Analytics tab with quarter-based genre evolution and a plain-language trend summary.
- Automations tab:
  - create genre-based rules from `Liked Songs` or any playlist source
  - sync manually per rule or with `Sync All`
  - write exact-match results to private Spotify playlists (privacy enforced on sync)
- CSV export of filtered tracks.
- Local JSON caching for playlists, artists, and audio features.

## Prerequisites
- Python 3.11+
- Spotify Developer app credentials:
  - `SPOTIFY_CLIENT_ID`
  - `SPOTIFY_CLIENT_SECRET`
- Optional (recommended if using HTTPS redirect): ngrok

## Installation
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Environment Variables
Create `.env`:

```env
SPOTIFY_CLIENT_ID="your_client_id"
SPOTIFY_CLIENT_SECRET="your_client_secret"

# Redirect URI registered in Spotify dashboard
SPOTIFY_REDIRECT_URI="https://your-ngrok-domain.ngrok-free.app/callback"

# Local callback listener target (must match your ngrok forward port)
SPOTIFY_CALLBACK_BIND_HOST="127.0.0.1"
SPOTIFY_CALLBACK_BIND_PORT="8890"
```

## Spotify Redirect Setup
1. In Spotify Developer Dashboard, add your redirect URI exactly:
   - Example: `https://your-ngrok-domain.ngrok-free.app/callback`
2. Start ngrok forwarding to your callback bind port:
```bash
ngrok http --url=your-ngrok-domain.ngrok-free.app 8890
```
3. Confirm `.env` values match both the Spotify redirect URI and local bind port.

## Run the App
```bash
source .venv/bin/activate
streamlit run app.py
```

Then:
1. Click `Login with Spotify`.
2. Choose a playlist.
3. Apply filters.
4. Use Explore / Analytics / Automations tabs.

## Project Structure
- `app.py` - Streamlit UI and app flow.
- `src/auth.py` - OAuth and local callback flow.
- `src/spotify_api.py` - Spotify API wrappers.
- `src/enrichment.py` - Track normalization and enrichment.
- `src/filter_engine.py` - Filtering/scoring logic.
- `src/ui_helpers.py` - Playlist sorting and genre-chart data helpers.
- `src/automation_store.py` - persisted automation rules.
- `src/automation_engine.py` - rule evaluation + playlist sync.
- `src/cache_store.py` - JSON cache handling.
- `src/exporters.py` - CSV/DataFrame exports.
- `tests/` - unit tests.

## Authentication Troubleshooting
- `Failed to start local callback server ... port is free`:
  - Another process is already using `SPOTIFY_CALLBACK_BIND_PORT`.
  - Use `lsof -nP -iTCP:<PORT> -sTCP:LISTEN` to inspect.
  - Change `SPOTIFY_CALLBACK_BIND_PORT` and update ngrok forward target.

- Spotify redirect mismatch errors:
  - `SPOTIFY_REDIRECT_URI` in `.env` must exactly match the URI configured in Spotify dashboard.

- Token/scope issues after changing scopes:
```bash
rm -f .spotify_cache
```
Then re-login.

- Automations need additional write scope:
  - `playlist-modify-private`
  - If sync buttons are disabled, reconnect to refresh token scopes.

## Playback Notes
- The app uses Spotify embed (`open.spotify.com/embed/track/...`).
- Playback behavior depends on Spotify/browser session context and Spotify availability by region/account.
- If a track cannot be embedded, use the Spotify link in the results table.
