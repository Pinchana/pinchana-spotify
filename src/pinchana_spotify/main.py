"""Spotify music downloader plugin.

Resolves Spotify URLs via the Spotify Web API, searches YouTube Music
for the best matching track, and downloads audio via yt-dlp.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re

from fastapi import APIRouter, FastAPI, HTTPException
from spotipy import Spotify
from spotipy.oauth2 import SpotifyClientCredentials
from ytmusicapi import YTMusic

from pinchana_core.models import ScrapeRequest, ScrapeResponse
from pinchana_core.music import MusicDownloader, MusicDownloadError, RateLimitError
from pinchana_core.plugins import ScraperPlugin, registry
from pinchana_core.storage import MediaStorage
from pinchana_core.vpn import GluetunController, VpnRotationError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()
gluetun = GluetunController()
storage = MediaStorage(
    base_path=os.getenv("CACHE_PATH", "./cache"),
    max_size_gb=float(os.getenv("CACHE_MAX_SIZE_GB", "10.0")),
)
proxy = os.getenv("PROXY")

# Spotify credentials
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")


def _get_spotify_client() -> Spotify | None:
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None
    return Spotify(
        client_credentials_manager=SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
        )
    )


def _search_ytmusic(query: str) -> str | None:
    """Search YouTube Music and return the best videoId."""
    try:
        ytm = YTMusic()
        results = ytm.search(query, filter="songs", limit=5)
        for r in results:
            vid = r.get("videoId")
            if vid:
                return vid
    except Exception as e:
        logger.warning("YTMusic search failed: %s", e)
    return None


class SpotifyDownloader(MusicDownloader):
    """Spotify → YT Music search → yt-dlp download."""

    async def resolve(self, url: str) -> tuple[str, dict]:
        sp = _get_spotify_client()
        if not sp:
            raise MusicDownloadError("Spotify API credentials not configured")

        loop = asyncio.get_running_loop()

        # Parse URL type
        track_match = re.search(r"track/([\w-]+)", url)
        album_match = re.search(r"album/([\w-]+)", url)

        if track_match:
            track_id = track_match.group(1)
            try:
                track = await loop.run_in_executor(None, lambda: sp.track(track_id))
            except Exception as e:
                status = getattr(e, "http_status", None)
                if status in (401, 403, 429) or any(s in str(e).lower() for s in ("429", "rate limit", "too many requests", "timeout", "connection")):
                    raise RateLimitError(f"Spotify track API blocked: {e}")
                raise MusicDownloadError(f"Spotify track API failed: {e}")
            if not track:
                raise MusicDownloadError("Spotify track not found")

            title = track["name"]
            artist = ", ".join(a["name"] for a in track["artists"])
            album = track["album"]["name"]
            duration = track["duration_ms"] // 1000
            cover_url = track["album"]["images"][0]["url"] if track["album"]["images"] else None

            query = f"{artist} {title} official audio"
            video_id = await loop.run_in_executor(None, _search_ytmusic, query)
            if not video_id:
                raise MusicDownloadError("No YouTube Music match found")

            meta = {
                "id": f"sp-{track_id}",
                "title": title,
                "artist": artist,
                "album": album,
                "duration": duration,
                "cover_url": cover_url,
            }
            return f"https://www.youtube.com/watch?v={video_id}", meta

        if album_match:
            album_id = album_match.group(1)
            try:
                album_data = await loop.run_in_executor(None, lambda: sp.album(album_id))
            except Exception as e:
                status = getattr(e, "http_status", None)
                if status in (401, 403, 429) or any(s in str(e).lower() for s in ("429", "rate limit", "too many requests", "timeout", "connection")):
                    raise RateLimitError(f"Spotify album API blocked: {e}")
                raise MusicDownloadError(f"Spotify album API failed: {e}")
            if not album_data:
                raise MusicDownloadError("Spotify album not found")

            album_name = album_data["name"]
            artist_name = ", ".join(a["name"] for a in album_data["artists"])
            cover_url = album_data["images"][0]["url"] if album_data["images"] else None

            tracks = album_data.get("tracks", {}).get("items", [])
            if not tracks:
                raise MusicDownloadError("Album has no tracks")

            # For now, download the first track as a representative
            # Full album support can be added with tracklist response
            first = tracks[0]
            title = first["name"]
            duration = first["duration_ms"] // 1000

            query = f"{artist_name} {title} official audio"
            video_id = await loop.run_in_executor(None, _search_ytmusic, query)
            if not video_id:
                raise MusicDownloadError("No YouTube Music match found for album track")

            meta = {
                "id": f"sp-album-{album_id}",
                "title": title,
                "artist": artist_name,
                "album": album_name,
                "duration": duration,
                "cover_url": cover_url,
            }
            return f"https://www.youtube.com/watch?v={video_id}", meta

        raise MusicDownloadError("Unsupported Spotify URL type")


sp_downloader = SpotifyDownloader(storage.base_path, proxy=proxy, gluetun=gluetun)


@router.post("/scrape", response_model=ScrapeResponse)
async def process_scrape_request(request: ScrapeRequest):
    url = str(request.url)
    if "open.spotify.com" not in url:
        raise HTTPException(status_code=400, detail="Invalid Spotify URL")

    try:
        mp3_path, meta = await sp_downloader.download(url)
    except MusicDownloadError as e:
        raise HTTPException(status_code=503, detail=str(e))

    shortcode = meta.get("id", "sp")
    post_dir = storage._post_dir(shortcode)

    # MusicDownloader already created post_dir, cover.jpg, and {id}.mp3
    dest_mp3 = post_dir / "audio.mp3"
    dest_cover = post_dir / "cover.jpg"
    if mp3_path != dest_mp3:
        mp3_path.rename(dest_mp3)

    response = ScrapeResponse(
        shortcode=shortcode,
        caption=meta.get("title", ""),
        author=meta.get("artist", ""),
        media_type="audio",
        thumbnail_url=f"/media/spotify/{shortcode}/cover.jpg" if dest_cover.exists() else "",
        audio_url=f"/media/spotify/{shortcode}/audio.mp3",
        cover_url=f"/media/spotify/{shortcode}/cover.jpg" if dest_cover.exists() else None,
        duration=int(meta.get("duration", 0)) if meta.get("duration") else None,
        title=meta.get("title"),
        album=meta.get("album"),
    )
    storage.save_metadata(shortcode, response.model_dump())
    return response


@router.get("/health")
async def health_check():
    try:
        status = await gluetun.get_vpn_status()
        vpn_status = status.get("status", "").lower()
        if gluetun.enabled and vpn_status != "running":
            raise HTTPException(status_code=503, detail=f"VPN not running: {vpn_status}")
        spotify_ok = _get_spotify_client() is not None
        return {"status": "healthy", "service": "spotify", "vpn": status, "spotify_api": spotify_ok}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Health check failed: {e}")


registry.register(
    ScraperPlugin(
        name="spotify",
        router=router,
        route_patterns=["open.spotify.com"],
    )
)

app = FastAPI(title="Pinchana Spotify", version="0.1.0")
app.include_router(router)
