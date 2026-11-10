import asyncio
from unittest.mock import patch

import httpx

from pinchana_core.storage import MediaStorage, set_cache_scope
from pinchana_spotify import main


def test_scoped_route_creates_audio_and_cover_in_the_same_directory(tmp_path):
    async def resolve(self, url):
        await asyncio.sleep(0)
        return url, {"id": "ojIf7BPOFvM", "title": "Track", "artist": "Artist", "cover_url": "https://example.com/cover.jpg"}
    async def download(self, url, post_dir):
        path = post_dir / "raw.audio"
        path.write_bytes(b"raw")
        return path
    async def cover(self, url, dest):
        dest.write_bytes(b"cover")
        return True
    async def convert(self, raw, dest, **kwargs):
        await asyncio.sleep(0)
        dest.write_bytes(b"mp3")
    async def run():
        storage = MediaStorage(tmp_path)
        with patch.object(main, "storage", storage), \
             patch.object(main.SpotifyDownloader, "resolve", resolve), \
             patch.object(main.SpotifyDownloader, "_ytdlp_download", download), \
             patch.object(main.SpotifyDownloader, "_download_cover", cover), \
             patch.object(main.SpotifyDownloader, "_to_mp3", convert):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                body = {"url": "https://open.spotify.com/track/test-track", "cache_scope": "a"*32}
                replies = await asyncio.gather(client.post("/scrape", json=body), client.post("/scrape", json=body))
                assert all(reply.status_code == 200 for reply in replies)
                assert replies[0].json() == replies[1].json()
                assert replies[0].json()["cover_url"].endswith("cover.jpg")
                assert (tmp_path / ".transient" / ("a"*32) / "ojIf7BPOFvM" / "audio.mp3").read_bytes() == b"mp3"
                assert (tmp_path / ".transient" / ("a"*32) / "ojIf7BPOFvM" / "cover.jpg").read_bytes() == b"cover"
                assert not (tmp_path / "ojIf7BPOFvM").exists()
                unscoped = await client.post("/scrape", json={"url": body["url"]})
                assert unscoped.status_code == 200
                assert (tmp_path / "ojIf7BPOFvM" / "audio.mp3").is_file()
        await storage.close()
    try:
        asyncio.run(run())
    finally:
        set_cache_scope(None)
