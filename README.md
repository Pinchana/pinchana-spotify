# Pinchana Spotify

This FastAPI module resolves supported public Spotify tracks and playlists and prepares audio through the shared Pinchana music workflow.

## Required configuration

```ini
SPOTIFY_CLIENT_ID=REPLACE_WITH_CLIENT_ID
SPOTIFY_CLIENT_SECRET=REPLACE_WITH_CLIENT_SECRET
```

The service health response reports whether credentials are configured. Store both values only in protected server configuration.

## API

- `POST /scrape` accepts `{"url":"https://open.spotify.com/track/TRACK_ID"}`.
- `GET /health` reports service, VPN, and credential readiness.

Clients normally use the gateway's authenticated `POST /v1/scrape` route.

## Development

```sh
uv sync --frozen
uv run uvicorn pinchana_spotify.main:app --host 0.0.0.0 --port 8086 --reload
```

```sh
# Run from the parent pinchana-api directory.
docker build --file pinchana-spotify/Dockerfile --tag pinchana-spotify:local .
```
