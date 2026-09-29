import os
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gacha_link as gl  # noqa: E402

OLD = b"https://public-operation-hk4e-sg.hoyoverse.com/gacha_info/api/getGachaLog?authkey=OLD&gacha_type=301&page=1&end_id=0"
NEW = b"https://public-operation-hk4e-sg.hoyoverse.com/gacha_info/api/getGachaLog?authkey=NEW&gacha_type=301&page=2&end_id=123"
PAGE = b"https://gs.hoyoverse.com/genshin/event/e20190909gacha-v3/index.html?authkey=PAGE&lang=en#/log"
NOISE = b"https://example.com/other?x=1"


def fake_cache(*urls: bytes) -> bytes:
    return b"\x00\x01".join(b"1/0/" + u + b"\x00\xff" for u in urls)


class ParseTests(unittest.TestCase):
    def test_prefers_last_api_url(self):
        blob = fake_cache(OLD, PAGE, NEW, NOISE)
        self.assertEqual(gl.pick_latest(gl.extract_gacha_urls(blob)), NEW.decode())

    def test_falls_back_to_page_url(self):
        self.assertEqual(gl.pick_latest(gl.extract_gacha_urls(fake_cache(NOISE, PAGE))), PAGE.decode())

    def test_none_when_missing(self):
        self.assertIsNone(gl.pick_latest(gl.extract_gacha_urls(fake_cache(NOISE))))

    def test_clean_url_drops_paging(self):
        cleaned = gl.clean_url(NEW.decode())
        self.assertNotIn("end_id", cleaned)
        self.assertNotIn("page=", cleaned)
        self.assertIn("authkey=NEW", cleaned)

    def test_log_path_parsing(self):
        with TemporaryDirectory() as tmp:
            game = gl.GAMES["genshin"]
            log = "Warmup file C:/Games/Genshin Impact game/GenshinImpact_Data/StreamingAssets/x.blk\n"
            self.assertEqual(gl.find_data_dir_in_log(log, game),
                             Path("C:/Games/Genshin Impact game/GenshinImpact_Data"))
            hsr = "Loading player data from D:/HoYo/Star Rail/Game/StarRail_Data/data.unity3d\n"
            self.assertEqual(gl.find_data_dir_in_log(hsr, gl.GAMES["hsr"]),
                             Path("D:/HoYo/Star Rail/Game/StarRail_Data"))


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.data = Path(self.tmp.name) / "GenshinImpact_Data"
        old = self.data / "webCaches/2.20.0.0/Cache/Cache_Data"
        new = self.data / "webCaches/2.31.0.0/Cache/Cache_Data"
        old.mkdir(parents=True)
        new.mkdir(parents=True)
        (old / "data_2").write_bytes(fake_cache(NOISE))
        self.cache = new / "data_2"
        self.cache.write_bytes(fake_cache(OLD))
        os.utime(old / "data_2", (1, 1))

    def tearDown(self):
        self.tmp.cleanup()

    def test_fetch_picks_newest_cache(self):
        self.assertEqual(gl.find_cache_file(self.data), self.cache)
        rc = gl.main(["genshin", "--path", str(Path(self.tmp.name)), "--no-copy"])
        self.assertEqual(rc, 0)

    def test_watch_detects_new_link(self):
        def write_later():
            time.sleep(0.3)
            self.cache.write_bytes(fake_cache(OLD, NEW))

        threading.Thread(target=write_later).start()
        rc = gl.main(["genshin", "watch", "--path", str(self.data), "--no-copy",
                      "--interval", "0.1", "--timeout", "5"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
