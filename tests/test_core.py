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


class HudOcrTest(unittest.TestCase):
    def test_m30t_panel_text(self):
        from app.aircraft.dji_m30t_hud import parse_m30t_hud_text

        text = """
目标纬度 41.856954
云台信息                 经度 81.664126
俯角: 10.90deg      H: 1413.8
原始俯角: 10.90deg     X: 4635961.8
方位角: 356.60deg      Y: 14555146.6
无人机纬度 41.856952
无人机经度 81.664127
"""
        parsed = parse_m30t_hud_text(text)
        self.assertAlmostEqual(parsed["pitch_down"], 10.90, places=1)
        self.assertAlmostEqual(parsed["yaw"], 356.60, places=1)
        self.assertAlmostEqual(parsed["lat"], 41.856952, places=5)
        self.assertAlmostEqual(parsed["lon"], 81.664127, places=5)

    def test_hz_gcs_panel_text(self):
        from app.aircraft.hz_gcs_hud import parse_hz_gcs_text
        from app.aircraft.registry import get_model

        text = """
状态管理
飞机状态
航向 359.0°
相对高度 1000.0m
位姿信息
经度 119.008178
纬度 31.761480
海拔高度 1000.0m
航向角 359.0°
俯仰角 0.0°
横滚角 1.0°
吊舱状态
方位 0.0°
俯仰 -90.0°
综合信息
"""
        parsed = parse_hz_gcs_text(text)
        self.assertAlmostEqual(parsed["lon"], 119.008178, places=5)
        self.assertAlmostEqual(parsed["lat"], 31.761480, places=5)
        self.assertAlmostEqual(parsed["rel_alt"], 1000.0, places=1)
        self.assertAlmostEqual(parsed["yaw"], 359.0, places=1)
        self.assertAlmostEqual(parsed["pitch"], -90.0, places=1)
        model = get_model("hz_gcs")
        self.assertIsNotNone(model)
        self.assertEqual(model.label.startswith("HZ"), True)

    def test_hz_gcs_ocr_dropped_decimal(self):
        from app.aircraft.hz_gcs_hud import parse_hz_gcs_text

        # 模拟 Tesseract 丢掉小数点
        text = """
位姿信息
经度 119008178
纬度 31761480
航向角 3590
吊舱状态
俯仰 -900
飞机状态
相对高度 10000m
"""
        parsed = parse_hz_gcs_text(text)
        self.assertAlmostEqual(parsed["lon"], 119.008178, places=4)
        self.assertAlmostEqual(parsed["lat"], 31.761480, places=4)
        self.assertAlmostEqual(parsed["yaw"], 359.0, places=1)
        self.assertAlmostEqual(parsed["pitch"], -90.0, places=1)
        self.assertAlmostEqual(parsed["rel_alt"], 1000.0, places=1)

    def test_hz_gcs_classify_hits_adaptive(self):
        from app.aircraft.hz_gcs_hud import _NumHit, _classify_hits

        hits = [
            _NumHit(119008178, "119008178", 0.80, 0.23, 80),
            _NumHit(31761480, "31761480", 0.80, 0.27, 80),
            _NumHit(1000.0, "1000.0m", 0.65, 0.31, 70),
            _NumHit(359.0, "359.0", 0.65, 0.22, 90),
            _NumHit(900, "900\"", 0.95, 0.31, 70),
        ]
        parsed = _classify_hits(hits)
        self.assertAlmostEqual(parsed["lon"], 119.008178, places=4)
        self.assertAlmostEqual(parsed["lat"], 31.761480, places=4)
        self.assertAlmostEqual(parsed["rel_alt"], 1000.0, places=1)
        self.assertAlmostEqual(parsed["yaw"], 359.0, places=1)
        self.assertAlmostEqual(parsed["pitch"], -90.0, places=1)

        # 样图 OCR 噪声：假纬度整数、0.0、359000001
        noisy = [
            _NumHit(20420011, "20420011"),
            _NumHit(119008178, "119008178"),
            _NumHit(31.761480, "31.761480"),
            _NumHit(0.0, "0.0"),
            _NumHit(10000, "10000"),
            _NumHit(-90.0, "-90.0"),
            _NumHit(350, "350"),
            _NumHit(1280, "1280"),
            _NumHit(359000001, "359000001"),
        ]
        parsed2 = _classify_hits(noisy)
        self.assertAlmostEqual(parsed2["lon"], 119.008178, places=4)
        self.assertAlmostEqual(parsed2["lat"], 31.761480, places=4)
        self.assertAlmostEqual(parsed2["rel_alt"], 1000.0, places=1)
        self.assertAlmostEqual(parsed2["yaw"], 359.0, places=0)
        self.assertAlmostEqual(parsed2["pitch"], -90.0, places=1)

    def test_merger_manual_height_with_ocr(self):
        from app.telemetry_merge import TelemetryMerger

        merger = TelemetryMerger(require_height=True, default_hfov=64, default_vfov=48)
        merger.ocr = {"lat": 41.85, "lon": 81.66, "yaw": 356.6, "pitch": -10.9}
        telem, status, missing = merger.merge()
        self.assertIsNone(telem)
        self.assertIn("height_agl", missing)
        merger.set_manual({"height_agl": 80}, locked=["height_agl"])
        telem, status, missing = merger.merge()
        self.assertIsNotNone(telem)
        self.assertEqual(missing, [])
        self.assertAlmostEqual(telem.height_agl, 80)
        self.assertEqual(status["height_agl"], "manual")
        self.assertEqual(status["lat"], "ocr")


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
