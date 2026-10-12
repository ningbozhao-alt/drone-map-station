"""最小 MQTT 3.1.1 订阅端，只收 QoS 0 的 OSD。"""

from __future__ import annotations

import socket
import threading
import time
from typing import Callable


def _encode_length(n: int) -> bytes:
    out = bytearray()
    while True:
        digit = n % 128
        n //= 128
        if n:
            digit |= 0x80
        out.append(digit)
        if not n:
            return bytes(out)


def _encode_utf(text: str) -> bytes:
    raw = text.encode()
    return len(raw).to_bytes(2, "big") + raw


def _read_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("mqtt closed")
        buf += chunk
    return bytes(buf)


def _read_packet(sock: socket.socket) -> tuple[int, bytes]:
    header = _read_exact(sock, 1)[0]
    value = 0
    multiplier = 1
    for _ in range(4):
        digit = _read_exact(sock, 1)[0]
        value += (digit & 127) * multiplier
        if not (digit & 128):
            break
        multiplier *= 128
    payload = _read_exact(sock, value) if value else b""
    return header, payload


def _connect_packet(client_id: str) -> bytes:
    body = _encode_utf("MQTT") + bytes([4, 0x02, 0x00, 30]) + _encode_utf(client_id)
    return bytes([0x10]) + _encode_length(len(body)) + body


def _subscribe_packet(topic: str) -> bytes:
    body = (1).to_bytes(2, "big") + _encode_utf(topic) + bytes([0])
    return bytes([0x82]) + _encode_length(len(body)) + body


def encode_publish(topic: str, payload: bytes) -> bytes:
    body = _encode_utf(topic) + payload
    return bytes([0x30]) + _encode_length(len(body)) + body


def parse_publish(payload: bytes) -> tuple[str, bytes]:
    topic_len = int.from_bytes(payload[:2], "big")
    topic = payload[2 : 2 + topic_len].decode()
    return topic, payload[2 + topic_len :]


class MqttSubscriber(threading.Thread):
    def __init__(self, host: str, port: int, topic: str, on_message: Callable[[str, bytes], None]):
        super().__init__(daemon=True, name="mqtt-osd")
        self.host = host
        self.port = port
        self.topic = topic
        self.on_message = on_message
        self.connected = False
        self.last_rx = 0.0
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            try:
                self._session()
            except Exception:
                self.connected = False
                self._stop.wait(3.0)

    def _session(self):
        sock = socket.create_connection((self.host, self.port), timeout=5)
        sock.settimeout(2.0)
        try:
            sock.sendall(_connect_packet(f"drape-{int(time.time()) % 100000}"))
            header, _ = _read_packet(sock)
            if header >> 4 != 2:
                raise ConnectionError("no connack")
            sock.sendall(_subscribe_packet(self.topic))
            self.connected = True
            while not self._stop.is_set():
                try:
                    header, body = _read_packet(sock)
                except socket.timeout:
                    continue
                kind = header >> 4
                if kind == 3:
                    topic, payload = parse_publish(body)
                    self.last_rx = time.time()
                    self.on_message(topic, payload)
                elif kind == 9:
                    continue
                elif kind == 13:
                    sock.sendall(bytes([0xD0, 0x00]))
        finally:
            self.connected = False
            sock.close()
