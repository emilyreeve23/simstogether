"""Sims Together LAN v0.1: independent, text-only LAN lobby protocol.
Not compatible with SimSync, and does not interface with The Sims 4.
"""
import json
import base64
import hashlib
import uuid
from pathlib import Path
import queue
import secrets
import socket
import threading
import time

PORT = 51130
MAX_LINE = 8192
PROTOCOL = 2
MAX_FILE = 2 * 1024 * 1024
CHUNK = 3072
TRANSFER_TYPES = {"file_offer", "file_accept", "file_reject", "file_chunk", "file_done"}


def _send(sock, message):
    raw = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(raw) > MAX_LINE:
        raise ValueError("Message too large")
    sock.sendall(raw)


def _read_messages(sock, stop):
    buffer = bytearray()
    while not stop.is_set():
        try:
            part = sock.recv(4096)
        except socket.timeout:
            continue
        if not part:
            return
        buffer.extend(part)
        if len(buffer) > MAX_LINE * 2:
            raise ValueError("Incoming message too large")
        while b"\n" in buffer:
            raw, _, rest = buffer.partition(b"\n")
            buffer = bytearray(rest)
            if len(raw) > MAX_LINE:
                raise ValueError("Incoming message too large")
            try:
                obj = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(obj, dict):
                yield obj


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 80))  # No packets sent; asks OS for preferred interface.
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class LobbyServer:
    def __init__(self, port=PORT, room_code=None, events=None):
        self.port = port
        self.room_code = room_code or f"{secrets.randbelow(1000000):06d}"
        self.events = events or queue.Queue()
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.peers = {}  # socket -> display name
        self.send_lock = threading.RLock()
        self.listener = None
        self.accept_thread = None

    def start(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", self.port))
        srv.listen(8)
        srv.settimeout(0.4)
        self.listener = srv
        self.port = srv.getsockname()[1]
        self.accept_thread = threading.Thread(target=self._accept, daemon=True)
        self.accept_thread.start()
        self.events.put(("server", f"Hosting on port {self.port}"))

    def _accept(self):
        while not self.stop.is_set():
            try:
                conn, address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.settimeout(0.5)
            threading.Thread(target=self._peer, args=(conn, address), daemon=True).start()

    def _peer(self, conn, address):
        name = None
        try:
            for msg in _read_messages(conn, self.stop):
                if name is None:
                    if msg.get("type") != "hello" or msg.get("version") != PROTOCOL or msg.get("code") != self.room_code:
                        _send(conn, {"type": "error", "text": "Wrong room code or incompatible version"})
                        return
                    proposed = str(msg.get("name", "Player")).strip()[:24] or "Player"
                    with self.lock:
                        existing = set(self.peers.values())
                        if proposed in existing:
                            _send(conn, {"type": "error", "text": "That player name is already in use"})
                            return
                        name = proposed
                        self.peers[conn] = name
                        players = list(self.peers.values())
                    _send(conn, {"type": "welcome", "players": players, "version": PROTOCOL})
                    self._broadcast({"type": "players", "players": players})
                    self._broadcast({"type": "notice", "text": f"{name} joined"})
                    self.events.put(("player", f"{name} connected from {address[0]}"))
                elif msg.get("type") == "chat":
                    text = str(msg.get("text", "")).strip()[:500]
                    if text:
                        self._broadcast({"type": "chat", "from": name, "text": text})
                elif msg.get("type") in TRANSFER_TYPES:
                    target = str(msg.get("to", ""))
                    with self.lock:
                        destination = next((peer for peer, display in self.peers.items() if display == target), None)
                    if destination and destination is not conn:
                        packet = dict(msg)
                        packet["from"] = name
                        try:
                            with self.send_lock:
                                _send(destination, packet)
                        except OSError:
                            pass
                elif msg.get("type") == "ping":
                    _send(conn, {"type": "pong", "at": msg.get("at")})
        except (OSError, ValueError):
            pass
        finally:
            with self.lock:
                gone = self.peers.pop(conn, None)
                players = list(self.peers.values())
            try:
                conn.close()
            except OSError:
                pass
            if gone:
                self.events.put(("player", f"{gone} disconnected"))
                self._broadcast({"type": "players", "players": players})
                self._broadcast({"type": "notice", "text": f"{gone} left"})

    def _broadcast(self, msg):
        with self.lock:
            peers = list(self.peers)
        for peer in peers:
            try:
                with self.send_lock:
                    _send(peer, msg)
            except OSError:
                pass

    def close(self):
        self.stop.set()
        if self.listener:
            try:
                self.listener.close()
            except OSError:
                pass
        with self.lock:
            peers = list(self.peers)
        for peer in peers:
            try:
                peer.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                peer.close()
            except OSError:
                pass


class LobbyClient:
    def __init__(self, events=None):
        self.events = events or queue.Queue()
        self.sock = None
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.connected = False
        self.thread = None
        self.transfer_waiters = {}
        self.transfer_lock = threading.Lock()

    def connect(self, host, port, code, name):
        self.stop.clear()
        self.thread = threading.Thread(target=self._run, args=(host, port, code, name), daemon=True)
        self.thread.start()

    def _run(self, host, port, code, name):
        try:
            s = socket.create_connection((host, port), timeout=5)
            s.settimeout(0.5)
            self.sock = s
            self._write({"type": "hello", "version": PROTOCOL, "code": code, "name": name})
            for msg in _read_messages(s, self.stop):
                kind = msg.get("type")
                if kind == "welcome":
                    self.connected = True
                    self.events.put(("connected", "Connected to lobby"))
                    self.events.put(("players", msg.get("players", [])))
                elif kind == "players":
                    self.events.put(("players", msg.get("players", [])))
                elif kind == "chat":
                    self.events.put(("chat", (msg.get("from", "?"), msg.get("text", ""))))
                elif kind == "file_offer":
                    self.events.put(("file_offer", msg))
                elif kind in ("file_chunk", "file_done"):
                    self.events.put((kind, msg))
                elif kind in ("file_accept", "file_reject"):
                    transfer_id = str(msg.get("id", ""))
                    with self.transfer_lock:
                        waiter = self.transfer_waiters.get(transfer_id)
                        if waiter and msg.get("from") == waiter["to"]:
                            waiter["accepted"] = kind == "file_accept"
                            waiter["signal"].set()
                    self.events.put(("transfer_reply", msg))
                elif kind in ("notice", "error"):
                    self.events.put((kind, msg.get("text", "")))
                    if kind == "error":
                        break
        except (OSError, ValueError) as exc:
            if not self.stop.is_set():
                self.events.put(("error", f"Connection failed: {exc}"))
        finally:
            self.connected = False
            self.close()
            self.events.put(("disconnected", "Disconnected"))

    def _write(self, msg):
        with self.lock:
            if self.sock:
                _send(self.sock, msg)

    def chat(self, text):
        if not self.connected:
            return
        try:
            self._write({"type": "chat", "text": text[:500]})
        except OSError as exc:
            self.events.put(("error", str(exc)))

    def transmit(self, path, recipient):
        """Send a manually chosen small file after recipient explicitly accepts."""
        path = Path(path)
        size = path.stat().st_size
        if size > MAX_FILE:
            raise ValueError("File exceeds 2 MB test limit")
        token = uuid.uuid4().hex
        signal = threading.Event()
        waiter = {"signal": signal, "accepted": False, "to": recipient}
        with self.transfer_lock:
            self.transfer_waiters[token] = waiter
        try:
            self._write({"type": "file_offer", "to": recipient, "id": token, "name": path.name,
                         "size": size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            self.events.put(("transfer_status", "Waiting for " + recipient + " to accept..."))
            if not signal.wait(timeout=45):
                self.events.put(("transfer_status", "File request timed out."))
                return
            if not waiter["accepted"]:
                self.events.put(("transfer_status", "File declined."))
                return
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(CHUNK)
                    if not chunk:
                        break
                    if self.stop.is_set() or not self.connected:
                        raise ConnectionError("Disconnected during transfer")
                    self._write({"type": "file_chunk", "to": recipient, "id": token,
                                 "data": base64.b64encode(chunk).decode("ascii")})
            self._write({"type": "file_done", "to": recipient, "id": token})
            self.events.put(("transfer_status", "Sent " + path.name + " to " + recipient))
        except (OSError, ConnectionError, ValueError) as exc:
            self.events.put(("transfer_status", "Send failed: " + str(exc)))
        finally:
            with self.transfer_lock:
                self.transfer_waiters.pop(token, None)

    def file_decision(self, to, transfer_id, accept):
        self._write({"type": "file_accept" if accept else "file_reject", "to": to, "id": transfer_id})

    def close(self):
        self.stop.set()
        with self.transfer_lock:
            for waiter in self.transfer_waiters.values():
                waiter["signal"].set()
        s, self.sock = self.sock, None
        if s:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass
