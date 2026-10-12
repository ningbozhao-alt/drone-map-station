import json
import socket
import threading
import time
import unittest

import cv2
import numpy as np

from app.catalog import cue_marks, load_catalog
from app.compare import colorize_difference
from app.coord import distance_m, offset_wgs, out_of_china, wgs84_to_bd09, wgs84_to_gcj02
from app.geometry import photograph, project_corners, warp_north_up
from app.mqtt_client import MqttSubscriber, _read_packet, encode_publish
from app.telemetry import Telemetry, parse_osd, parse_srt


class CoordTest(unittest.TestCase):
    def test_china_offset_is_hundreds_of_meters(self):
        lon, lat = 120.15507, 30.274084
        glon, glat = wgs84_to_gcj02(lon, lat)
        blon, blat = wgs84_to_bd09(lon, lat)
        self.assertFalse(out_of_china(lon, lat))
        self.assertGreater(distance_m(lat, lon, glat, glon), 50)
        self.assertGreater(distance_m(lat, lon, blat, blon), distance_m(lat, lon, glat, glon))
        self.assertLess(distance_m(lat, lon, blat, blon), 1500)

    def test_outside_china_is_unchanged(self):
        lon, lat = -73.9857, 40.7484
        self.assertEqual(wgs84_to_gcj02(lon, lat), (lon, lat))


class GeometryTest(unittest.TestCase):
    def test_nadir_width_matches_fov(self):
        width, height, hfov, vfov, agl = 640, 480, 64.0, 48.0, 100.0
        foot = project_corners(width, height, hfov, vfov, 0, -90, 0, agl)
        self.assertEqual(foot.view, "nadir")
        easts = [c[0] for c in foot.corners_enu]
        norths = [c[1] for c in foot.corners_enu]
        self.assertAlmostEqual(sum(easts) / 4, 0, delta=0.2)
        self.assertAlmostEqual(sum(norths) / 4, 0, delta=0.2)
        expected = 2 * agl * np.tan(np.deg2rad(hfov / 2))
        self.assertAlmostEqual(max(easts) - min(easts), expected, delta=expected * 0.03)

    def test_oblique_quad_lies_ahead(self):
        foot = project_corners(640, 480, 64, 48, 0, -50, 0, 100)
        self.assertEqual(foot.view, "oblique")
        top = (foot.corners_enu[0][1] + foot.corners_enu[1][1]) / 2
        bottom = (foot.corners_enu[2][1] + foot.corners_enu[3][1]) / 2
        self.assertGreater(top, bottom)
        self.assertGreater(bottom, 0)
        widths = []
        for pair in ((0, 1), (3, 2)):
            a, b = foot.corners_enu[pair[0]], foot.corners_enu[pair[1]]
            widths.append(abs(a[0] - b[0]))
        self.assertGreater(abs(widths[0] - widths[1]), 5)

    def test_shallow_pitch_clips_sky_and_still_drapes(self):
        foot = project_corners(960, 540, 74, 45, -19.6, -17.8, 0, 8)
        self.assertTrue(foot.ok)
        self.assertGreater(min(p[1] for p in foot.src_px), 40)
        self.assertLess(max(c[1] for c in foot.corners_enu), 400)
        frame = np.zeros((540, 960, 3), np.uint8)
        frame[200:400, 300:600] = (40, 80, 200)
        drape = warp_north_up(frame, foot.corners_enu, 30.214, 120.084, src_px=foot.src_px)
        self.assertIsNotNone(drape)
        self.assertGreater(int(drape.image[:, :, 3].max()), 200)

    def test_roll_moves_corners(self):
        a = project_corners(640, 480, 64, 48, 0, -90, 0, 100)
        b = project_corners(640, 480, 64, 48, 0, -90, 30, 100)
        self.assertNotAlmostEqual(a.corners_enu[0][0], b.corners_enu[0][0], delta=1)

    def test_photo_drapes_back_to_mark(self):
        ground = np.zeros((400, 400, 3), np.uint8)
        ground[:] = (40, 120, 40)
        cv2.circle(ground, (200, 200), 6, (0, 0, 255), -1)
        photo = photograph(ground, -100, 100, 0.5, 0, 0, 80, 0, -90, 0, 60, 45, 320, 240)
        foot = project_corners(320, 240, 60, 45, 0, -90, 0, 80)
        drape = warp_north_up(photo, foot.corners_enu, 30.27, 120.15, max_px=360)
        self.assertIsNotNone(drape)
        h, w = drape.image.shape[:2]
        center = drape.image[h // 2, w // 2]
        self.assertGreater(int(center[2]), 180)
        self.assertGreater(int(center[3]), 200)


class CompareTest(unittest.TestCase):
    def test_difference_hits_new_water_only(self):
        pre = np.zeros((300, 300, 3), np.uint8)
        pre[:] = (70, 130, 72)
        current = pre.copy()
        current[120:180, 130:190] = (210, 90, 30)
        photo = photograph(current, -75, 75, 0.5, 0, 0, 80, 0, -90, 0, 60, 45, 200, 150)
        foot = project_corners(200, 150, 60, 45, 0, -90, 0, 80)
        lat, lon = 30.27, 120.15
        drape = warp_north_up(photo, foot.corners_enu, lat, lon, max_px=240)
        south, west = offset_wgs(lat, lon, -75, -75)
        north, east = offset_wgs(lat, lon, 75, 75)
        _, ratio = colorize_difference(drape, pre, {"west": west, "south": south, "east": east, "north": north})
        self.assertGreater(ratio, 0.02)
        self.assertLess(ratio, 0.45)


class TelemetryTest(unittest.TestCase):
    def test_srt(self):
        text = """1
00:00:01,000 --> 00:00:01,125
[latitude: 30.2740840] [longitude: 120.1550700] [rel_alt: 100.00 abs_alt: 120.00] [gb_yaw: 12.00 gb_pitch: -90.00 gb_roll: 0.00] [hfov: 64.00] [vfov: 48.00]
"""
        cues = parse_srt(text)
        self.assertEqual(len(cues), 1)
        self.assertAlmostEqual(cues[0].lat, 30.274084)
        self.assertAlmostEqual(cues[0].pitch, -90)
        self.assertAlmostEqual(cues[0].time_s, 1.0)
        self.assertAlmostEqual(cues[0].hfov, 64)

    def test_osd_nested_gimbal(self):
        payload = {
            "timestamp": 1_700_000_000_000,
            "data": {
                "latitude": 30.2,
                "longitude": 120.1,
                "elevation": 90,
                "height": 110,
                "attitude_head": 5,
                "52-0-0": {"gimbal_pitch": -60, "gimbal_roll": 1, "gimbal_yaw": 20, "zoom_factor": 2},
            },
        }
        telem = parse_osd(payload, 64, 48)
        self.assertAlmostEqual(telem.pitch, -60)
        self.assertAlmostEqual(telem.yaw, 20)
        self.assertAlmostEqual(telem.hfov, 32)
        self.assertAlmostEqual(telem.rel_alt, 90)


class MqttTest(unittest.TestCase):
    def test_subscriber_receives_osd(self):
        received = []
        ready = []

        def serve():
            srv = socket.socket()
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            ready.append(srv.getsockname()[1])
            conn, _ = srv.accept()
            _read_packet(conn)
            conn.sendall(bytes([0x20, 0x02, 0x00, 0x00]))
            _read_packet(conn)
            conn.sendall(bytes([0x90, 0x03, 0x00, 0x01, 0x00]))
            body = json.dumps({"data": {"latitude": 1.5, "longitude": 2.5, "elevation": 40, "gimbal_pitch": -88}}).encode()
            conn.sendall(encode_publish("thing/product/SN/osd", body))
            time.sleep(0.8)
            conn.close()
            srv.close()

        threading.Thread(target=serve, daemon=True).start()
        while not ready:
            time.sleep(0.01)
        sub = MqttSubscriber("127.0.0.1", ready[0], "thing/product/+/osd", lambda topic, payload: received.append(payload))
        sub.start()
        for _ in range(30):
            if received:
                break
            time.sleep(0.1)
        sub.stop()
        self.assertTrue(received)
        telem = parse_osd(json.loads(received[0].decode()))
        self.assertAlmostEqual(telem.lat, 1.5)
        self.assertAlmostEqual(telem.pitch, -88)


class CatalogTest(unittest.TestCase):
    def test_steepest_and_level_times(self):
        cues = [
            Telemetry(0, 0, 10, 10, 0, -40, 0, 60, 40),
            Telemetry(0, 0, 10, 10, 0, -90, 0, 60, 40),
            Telemetry(0, 0, 10, 10, 0, -20, 0, 60, 40),
        ]
        marks = cue_marks(cues, 8)
        self.assertAlmostEqual(marks["nadir_t"], 1 / 8)
        self.assertAlmostEqual(marks["oblique_t"], 2 / 8)
        self.assertAlmostEqual(marks["pitch_min"], -90)
        self.assertAlmostEqual(marks["pitch_max"], -20)

    def test_catalog_skips_missing_files(self):
        import tempfile
        from pathlib import Path

        root = Path(tempfile.mkdtemp())
        (root / "samples").mkdir()
        (root / "samples" / "ok.mp4").write_bytes(b"x" * 2048)
        (root / "samples" / "ok.srt").write_text("a", encoding="utf-8")
        (root / "samples" / "catalog.json").write_text(
            json.dumps(
                [
                    {"id": "ok", "label": "有", "video": "samples/ok.mp4", "srt": "samples/ok.srt"},
                    {"id": "gone", "label": "无", "video": "samples/no.mp4", "srt": "samples/no.srt"},
                ]
            ),
            encoding="utf-8",
        )
        clips = load_catalog(root)
        self.assertEqual([clip["ready"] for clip in clips], [True, False])


if __name__ == "__main__":
    unittest.main()
