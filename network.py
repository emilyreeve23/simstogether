"""Sims Together v0.5: LAN lobby + file/save transfer + compatibility summaries.
Separate from SimSync; in-game integration remains limited to the Stage 2 ping.
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

PORT = 51245
MAX_LINE = 8192
PROTOCOL = 3
MAX_FILE = 2 * 1024 * 1024
CHUNK = 3072
TRANSFER_TYPES = {"file_offer", "file_accept", "file_reject", "file_chunk", "file_done",
                  "save_offer", "save_accept", "save_reject", "save_chunk", "save_done", "save_verified", "compat_report"}
from save_tools import MAX_SAVE_BYTES, sha256_file


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
                elif msg.get("type") == "compat_report":
                    report = msg.get('report')
                    if not isinstance(report, dict):
                        continue
                    packet = {'type': 'compat_report', 'from': name, 'report': report}
                    with self.lock:
                        destinations = [peer for peer in self.peers if peer is not conn]
                    for destination in destinations:
                        try:
                            with self.send_lock:
                                _send(destination, packet)
                        except (OSError, ValueError):
                            pass
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
        self.save_receivers = {}
        self.save_lock = threading.RLock()

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
                elif kind == "save_offer":
                    self.events.put(("save_offer", msg))
                elif kind == "save_chunk":
                    self._save_chunk(msg)
                elif kind == "save_done":
                    self._finish_save(msg)
                elif kind == "save_verified":
                    self.events.put(('save_verified', msg))
                elif kind == "compat_report":
                    self.events.put(("compat_report", msg))
                elif kind in ("file_chunk", "file_done"):
                    self.events.put((kind, msg))
                elif kind in ("file_accept", "file_reject", "save_accept", "save_reject"):
                    transfer_id = str(msg.get("id", ""))
                    with self.transfer_lock:
                        waiter = self.transfer_waiters.get(transfer_id)
                        if waiter and msg.get("from") == waiter["to"]:
                            waiter["accepted"] = kind in ("file_accept", "save_accept")
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

    def report_compat(self, report):
        if not self.connected:
            raise ConnectionError('Not connected')
        self._write({'type': 'compat_report', 'report': report})

    def begin_save_receive(self, msg, storage):
        """Call from the UI thread *before* sending save_accept."""
        import re
        token = str(msg.get('id', ''))
        name = str(msg.get('name', ''))
        sender = str(msg.get('from', ''))
        expected = str(msg.get('sha256', ''))
        size = msg.get('size')
        if not (re.fullmatch(r'[0-9a-f]{32}', token) and
                re.fullmatch(r'Slot_[0-9a-fA-F]{8}\.save', name, re.I) and
                re.fullmatch(r'[0-9a-f]{64}', expected) and
                isinstance(size, int) and 1 <= size <= MAX_SAVE_BYTES and
                1 <= len(sender) <= 24):
            raise ValueError('Malformed or oversized save offer')
        target_dir = Path(storage)
        target_dir.mkdir(parents=True, exist_ok=True)
        # The received file is staged outside the game's active saves folder.
        label = time.strftime('%Y%m%d-%H%M%S') + '_' + token[:8] + '_' + name
        target = target_dir / label
        partial = target_dir / (label + '.partial')
        with self.save_lock:
            if token in self.save_receivers:
                raise ValueError('Duplicate transfer')
            handle = partial.open('xb')
            self.save_receivers[token] = {'from': sender, 'target': target, 'partial': partial,
                                           'handle': handle, 'size': size, 'name': name,
                                           'received': 0, 'hash': expected,
                                           'digest': hashlib.sha256(), 'started': time.monotonic(),
                                           'last_note': 0}
        self._write({'type': 'save_accept', 'to': sender, 'id': token})

    def decline_save(self, msg):
        self._write({'type': 'save_reject', 'to': str(msg.get('from', '')), 'id': str(msg.get('id', ''))})

    def _discard_save(self, token, reason):
        with self.save_lock:
            state = self.save_receivers.pop(token, None)
            if state:
                try:
                    state['handle'].close()
                finally:
                    try:
                        state['partial'].unlink()
                    except OSError:
                        pass
        if state:
            self.events.put(('save_status', reason))

    def _save_chunk(self, msg):
        token = str(msg.get('id', ''))
        with self.save_lock:
            state = self.save_receivers.get(token)
            if not state or msg.get('from') != state['from']:
                return
            try:
                data = base64.b64decode(str(msg.get('data', '')), validate=True)
                if not 0 < len(data) <= CHUNK:
                    raise ValueError('Invalid save chunk size')
                if state['received'] + len(data) > state['size']:
                    raise ValueError('Save transfer exceeded advertised size')
                if time.monotonic() - state['started'] > 1800:
                    raise ValueError('Save transfer timed out')
                state['handle'].write(data)
                state['digest'].update(data)
                state['received'] += len(data)
                pct = int(100 * state['received'] / state['size'])
                if pct >= state['last_note'] + 10:
                    state['last_note'] = pct
                    self.events.put(('save_status', 'Receiving %s: %s%%' % (state['name'], pct)))
            except (ValueError, OSError, base64.binascii.Error) as exc:
                self._discard_save(token, 'Rejected save data: ' + str(exc))

    def _finish_save(self, msg):
        token = str(msg.get('id', ''))
        with self.save_lock:
            state = self.save_receivers.get(token)
            if not state or msg.get('from') != state['from']:
                return
            state['handle'].flush()
            state['handle'].close()
            if state['received'] != state['size'] or state['digest'].hexdigest() != state['hash']:
                self._discard_save(token, 'Save checksum/size did not match; discarded.')
                return
            try:
                state['partial'].replace(state['target'])
            except OSError as exc:
                self._discard_save(token, 'Could not stage received save: ' + str(exc))
                return
            self.save_receivers.pop(token, None)
            self.events.put(('save_received', {'path': str(state['target']), 'from': state['from'],
                                               'hash': state['hash'], 'size': state['size']}))
            try:
                self._write({'type': 'save_verified', 'to': state['from'], 'id': token,
                             'sha256': state['hash']})
            except OSError:
                pass

    def transmit_save(self, path, recipient):
        """Send ONLY an already created, stable private save snapshot."""
        path = Path(path)
        if not self.connected:
            self.events.put(('save_status', 'Cannot send: not connected.'))
            return
        size = path.stat().st_size
        if not (0 < size <= MAX_SAVE_BYTES):
            self.events.put(('save_status', 'Save must be between 1 byte and 128 MB.'))
            return
        import re
        # Snapshots are named <timestamp>_Slot_...save; wire filename is only the slot filename.
        match = re.search(r'(Slot_[0-9a-fA-F]{8}\.save)\Z', path.name, re.I)
        if not match:
            self.events.put(('save_status', 'Not a Sims 4 slot save.'))
            return
        token = uuid.uuid4().hex
        signal = threading.Event()
        waiter = {'signal': signal, 'accepted': False, 'to': recipient}
        with self.transfer_lock:
            self.transfer_waiters[token] = waiter
        try:
            checksum = sha256_file(path)
            self._write({'type': 'save_offer', 'to': recipient, 'id': token,
                         'name': match.group(1), 'size': size, 'sha256': checksum})
            self.events.put(('save_status', 'Waiting for ' + recipient + ' to accept save...'))
            if not signal.wait(timeout=60):
                self.events.put(('save_status', 'Save request timed out.'))
                return
            if not waiter['accepted']:
                self.events.put(('save_status', 'Save was declined or connection closed.'))
                return
            sent = 0
            last = 0
            with path.open('rb') as handle:
                while True:
                    chunk = handle.read(CHUNK)
                    if not chunk:
                        break
                    if self.stop.is_set() or not self.connected:
                        raise ConnectionError('Disconnected during transfer')
                    self._write({'type': 'save_chunk', 'to': recipient, 'id': token,
                                 'data': base64.b64encode(chunk).decode('ascii')})
                    sent += len(chunk)
                    pct = int(100 * sent / size)
                    if pct >= last + 10:
                        last = pct
                        self.events.put(('save_status', 'Sending %s: %s%%' % (match.group(1), pct)))
            self._write({'type': 'save_done', 'to': recipient, 'id': token})
            self.events.put(('save_status', 'Save bytes sent. Waiting for receiver to verify.'))
        except (OSError, ConnectionError, ValueError) as exc:
            self.events.put(('save_status', 'Save transfer failed: ' + str(exc)))
        finally:
            with self.transfer_lock:
                self.transfer_waiters.pop(token, None)

    def close(self):
        self.stop.set()
        with self.save_lock:
            for token in list(self.save_receivers):
                self._discard_save(token, 'Partial save removed on disconnect.')
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
