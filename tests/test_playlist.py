import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from pinchana_core.models import ScrapeRequest
from pinchana_spotify.main import playlist_metadata


class SpotifyPlaylistTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_pages_and_both_item_wrappers(self):
        client = Mock()
        client.playlist.return_value = {"name": "Playlist"}
        client.playlist_items.side_effect = [
            {"items": [{"track": {"id": "first", "type": "track"}}], "next": "next"},
            {"items": [{"item": {"id": "second", "type": "track"}}], "next": None},
        ]
        with patch("pinchana_spotify.main._get_spotify_client", return_value=client):
            result = await playlist_metadata(ScrapeRequest(url="https://open.spotify.com/playlist/abc"))
        self.assertEqual(result["urls"], [
            "https://open.spotify.com/track/first", "https://open.spotify.com/track/second",
        ])
        self.assertEqual(client.playlist_items.call_args_list[1].kwargs["offset"], 1)

    async def test_album_returns_all_tracks_not_only_representative(self):
        client = Mock()
        client.album.return_value = {"name": "Album"}
        client.album_tracks.return_value = {"items": [{"id": "one"}, {"id": "two"}], "next": None}
        with patch("pinchana_spotify.main._get_spotify_client", return_value=client):
            result = await playlist_metadata(ScrapeRequest(url="https://open.spotify.com/album/abc"))
        self.assertEqual(len(result["urls"]), 2)

    async def test_limits_and_unavailable_items(self):
        client = Mock()
        client.playlist.return_value = {"name": "Playlist"}
        for items in [[{"track": None}], [{"track": {"id": "local", "is_local": True}}],
                      [{"track": {"id": str(index)}} for index in range(101)]]:
            client.playlist_items.side_effect = None
            client.playlist_items.return_value = {"items": items, "next": None}
            with patch("pinchana_spotify.main._get_spotify_client", return_value=client):
                with self.assertRaises(HTTPException) as failure:
                    await playlist_metadata(ScrapeRequest(url="https://open.spotify.com/playlist/abc"))
            self.assertEqual(failure.exception.status_code, 400)
