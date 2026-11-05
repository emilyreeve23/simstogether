"""Windows GUI for a *network-only* Sims Together proof of concept."""
import json
import hashlib
import base64
import re
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import queue
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from network import LobbyClient, LobbyServer, PORT, lan_ip, MAX_FILE

BASE = Path(__file__).resolve().parent
BRIDGE_PORT = 51246
SETTINGS = Path(os.environ.get("APPDATA", str(Path.home()))) / "SimsTogetherPrototype" / "settings.json"

class GameBridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path != '/status':
            self.send_error(404)
            return
        self.reply({'ok': True, 'app': 'SimsTogether', 'version': '0.4',
                    'lobby_connected': bool(self.server.launcher.client and self.server.launcher.client.connected)})

    def do_POST(self):
        if self.path != '/game-event':
            self.send_error(404)
            return
        try:
            n = int(self.headers.get('Content-Length', 0))
            if n <= 0 or n > 2048:
                self.send_error(400)
                return
            item = json.loads(self.rfile.read(n).decode('utf-8'))
            if not isinstance(item, dict) or item.get('event') != 'ping':
                self.send_error(400)
                return
            # Only event metadata is accepted; no commands are executed by the launcher.
            self.server.launcher.events.put(('game', 'In-game st.ping received from The Sims 4!'))
            self.reply({'ok': True, 'lobby_connected': bool(self.server.launcher.client and self.server.launcher.client.connected)})
        except (ValueError, UnicodeDecodeError):
            self.send_error(400)

    def reply(self, data):
        content = json.dumps(data).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)


class Launcher:
    def __init__(self, root):
        self.root = root
        root.title("Sims Together  |  v0.4 • LAN test")
        root.geometry("790x650")
        root.minsize(730, 580)
        self.events = queue.Queue()
        self.bridge = None
        self.bridge_status = tk.StringVar(value="Game bridge: starting...")
        self.server = None
        self.client = None
        self.settings = self.load_settings()
        self.name = tk.StringVar(value=self.settings.get("name", "Player1"))
        self.address = tk.StringVar(value=self.settings.get("address", "127.0.0.1"))
        self.port = tk.StringVar(value=str(self.settings.get("port", 51245)))
        self.code = tk.StringVar(value="")
        self.exe = tk.StringVar(value=self.settings.get("exe", ""))
        self.status = tk.StringVar(value="Not connected")
        self.players = tk.StringVar(value="No players connected")
        self.local = tk.StringVar(value=f"Preferred local IP: {lan_ip()} (check ipconfig if using Hamachi)")
        self.chat = tk.StringVar()
        self.file_target = tk.StringVar()
        self.incoming = {}
        self.build()
        self.start_game_bridge()
        root.after(100, self.drain)
        root.protocol("WM_DELETE_WINDOW", self.quit)

    @staticmethod
    def load_settings():
        try:
            return json.loads(SETTINGS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save_settings(self):
        try:
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS.write_text(json.dumps({"name":self.name.get(),"address":self.address.get(),"port":self.port.get(),"exe":self.exe.get()}, indent=2), encoding="utf-8")
        except OSError:
            pass

    def build(self):
        """A restrained, regular Windows-style application window."""
        self.root.configure(bg="#f3f4f6")
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.configure("TFrame", background="#f3f4f6")
        st.configure("TLabel", background="#f3f4f6", foreground="#242b36", font=("Segoe UI", 10))
        st.configure("Title.TLabel", font=("Segoe UI Semibold", 17), foreground="#222b38")
        st.configure("Section.TLabelframe", background="#f3f4f6")
        st.configure("Section.TLabelframe.Label", background="#f3f4f6", foreground="#394657", font=("Segoe UI Semibold", 10))
        st.configure("TEntry", padding=(6, 5), fieldbackground="white")
        st.configure("TButton", padding=(11, 6), font=("Segoe UI", 9))
        st.configure("Accent.TButton", background="#315f9c", foreground="white", padding=(12, 6))
        st.map("Accent.TButton", background=[("active", "#244a7c")])
        body = ttk.Frame(self.root, padding=(18, 14))
        body.pack(fill="both", expand=True)
        top = ttk.Frame(body)
        top.pack(fill="x", pady=(0, 13))
        ttk.Label(top, text="Sims Together", style="Title.TLabel").pack(side="left")
        ttk.Label(top, text="LAN test  ·  v0.4", foreground="#667282").pack(side="right", pady=8)

        conn = ttk.LabelFrame(body, text="Connection", style="Section.TLabelframe", padding=12)
        conn.pack(fill="x", pady=(0, 10))
        fields = ttk.Frame(conn)
        fields.pack(fill="x")
        for col, (label, var, width) in enumerate((("Your name", self.name, 16),
                                                    ("Host IP / Hamachi IP", self.address, 24),
                                                    ("Port", self.port, 9),
                                                    ("Room code", self.code, 10))):
            fields.columnconfigure(col, weight=2 if col == 1 else 1)
            area = ttk.Frame(fields)
            area.grid(row=0, column=col, sticky="ew", padx=(0, 10 if col < 3 else 0))
            ttk.Label(area, text=label).pack(anchor="w", pady=(0, 3))
            ttk.Entry(area, textvariable=var, width=width).pack(fill="x")
        tools = ttk.Frame(conn)
        tools.pack(fill="x", pady=(11, 0))
        ttk.Button(tools, text="Host", command=self.host, style="Accent.TButton").pack(side="left", padx=(0, 7))
        ttk.Button(tools, text="Join", command=self.join).pack(side="left", padx=(0, 7))
        ttk.Button(tools, text="Disconnect", command=self.disconnect).pack(side="left", padx=(0, 7))
        ttk.Button(tools, text="Copy code", command=lambda: self._copy(self.code.get())).pack(side="left")
        ttk.Label(tools, text="Local IP: " + lan_ip(), foreground="#5e6c7e").pack(side="right")

        info = ttk.Frame(body)
        info.pack(fill="x", pady=(0, 10))
        ttk.Label(info, text="Status:").pack(side="left")
        ttk.Label(info, textvariable=self.status, foreground="#315f9c").pack(side="left", padx=(4, 17))
        ttk.Label(info, textvariable=self.players).pack(side="left")

        game = ttk.LabelFrame(body, text="The Sims 4", padding=11)
        game.pack(fill="x", pady=(0, 10))
        ttk.Label(game, textvariable=self.bridge_status, foreground="#5d697b").pack(side="left")
        ttk.Button(game, text="Mods folder", command=self.open_mod_folder).pack(side="right", padx=(7, 0))
        ttk.Button(game, text="Launch game", command=self.launch_game).pack(side="right", padx=(7, 0))
        ttk.Button(game, text="Browse…", command=self.browse).pack(side="right")

        transfer = ttk.LabelFrame(body, text="Send a test file", padding=11)
        transfer.pack(fill="x", pady=(0, 10))
        ttk.Label(transfer, text="Send a file up to 2 MB. Your friend must accept it.").pack(side="left")
        ttk.Button(transfer, text="Choose file…", command=self.send_file).pack(side="right")
        self.target_combo = ttk.Combobox(transfer, textvariable=self.file_target, width=16, state="readonly")
        self.target_combo.pack(side="right", padx=9)
        ttk.Label(transfer, text="To:").pack(side="right")

        logs = ttk.LabelFrame(body, text="Session messages", padding=10)
        logs.pack(fill="both", expand=True)
        self.log_box = tk.Text(logs, wrap="word", height=8, state="disabled", bg="white", fg="#202a36",
                               font=("Consolas", 9), relief="solid", bd=1, padx=8, pady=7)
        self.log_box.pack(fill="both", expand=True)
        msgrow = ttk.Frame(logs)
        msgrow.pack(fill="x", pady=(8, 0))
        ttk.Entry(msgrow, textvariable=self.chat).pack(side="left", fill="x", expand=True, padx=(0, 7))
        ttk.Button(msgrow, text="Send message", command=self.send).pack(side="right")
        self.root.bind("<Return>", lambda e: self.send() if self.root.focus_get() and self.root.focus_get().winfo_class() == "TEntry" else None)
        ttk.Label(body, text="Testing only — this build does not synchronise gameplay, saves, mods or DLC.",
                  foreground="#737d89").pack(anchor="w", pady=(8, 0))
        self.log("Ready. Host or join on port 51245; use your Hamachi IP for remote friends.")
        self.log("In Sims 4, enter st.ping to test the game bridge.")

    def send_file(self):
        if not self.client or not self.client.connected:
            messagebox.showinfo("Not connected", "Join a lobby before sending files.")
            return
        to = self.file_target.get().strip()
        if not to:
            messagebox.showinfo("Choose recipient", "Choose another player in the To box.")
            return
        file_path = filedialog.askopenfilename(title="Choose a small test file (2 MB maximum)")
        if not file_path:
            return
        size = Path(file_path).stat().st_size
        if size > MAX_FILE:
            messagebox.showerror("File too large", "Use a file smaller than 2 MB for this test.")
            return
        if not messagebox.askyesno("Send test file", f"Send {Path(file_path).name} ({size:,} bytes) to {to}?"):
            return
        threading.Thread(target=self.client.transmit, args=(file_path, to), daemon=True).start()

    def offer_file(self, msg):
        sender = str(msg.get("from", ""))
        name = str(msg.get("name", ""))
        token = str(msg.get("id", ""))
        size = msg.get("size")
        checksum = str(msg.get("sha256", ""))
        if not (isinstance(size, int) and 0 <= size <= MAX_FILE and
                re.fullmatch(r"[a-f0-9]{64}", checksum) and
                re.fullmatch(r"[0-9a-f]{32}", token) and
                name and len(name) <= 180 and name not in (".", "..") and
                name == Path(name).name and not any(c in name for c in ("/", "\\", ":"))):
            self.log("Rejected malformed file offer.")
            return
        accepted = messagebox.askyesno("Incoming test file", f"{sender} wants to send:\n\n{name}\n{size:,} bytes\n\nSave to SimsTogether Received Files?", parent=self.root)
        if accepted:
            self.incoming[token] = {"from": sender, "name": name, "size": size,
                                    "hash": checksum, "data": bytearray(), "started": time.monotonic()}
        if self.client:
            self.client.file_decision(sender, token, accepted)
        self.log(("Accepted " if accepted else "Declined ") + name + " from " + sender)

    def receive_chunk(self, msg):
        token = str(msg.get("id", ""))
        state = self.incoming.get(token)
        if not state or state["from"] != msg.get("from"):
            return
        try:
            raw = base64.b64decode(str(msg.get("data", "")), validate=True)
            if len(raw) > 3072 or len(state["data"]) + len(raw) > state["size"] or time.monotonic() - state["started"] > 180:
                raise ValueError("Invalid or oversized chunk")
            state["data"].extend(raw)
        except (ValueError, base64.binascii.Error):
            self.incoming.pop(token, None)
            self.log("File transfer rejected: invalid data.")

    def finish_file(self, msg):
        token = str(msg.get("id", ""))
        state = self.incoming.pop(token, None)
        if not state or state["from"] != msg.get("from"):
            return
        data = bytes(state["data"])
        if len(data) != state["size"] or hashlib.sha256(data).hexdigest() != state["hash"]:
            self.log("File failed size/hash verification. Nothing saved.")
            return
        directory = Path.home() / "Downloads" / "SimsTogether Received Files"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            filename = state["name"]
            dest = directory / filename
            counter = 1
            while dest.exists():
                origin = Path(filename)
                dest = directory / f"{origin.stem} ({counter}){origin.suffix}"
                counter += 1
            with dest.open("xb") as f:
                f.write(data)
            self.log(f"Received {dest.name} — saved to {directory}")
            messagebox.showinfo("File received", f"Saved to:\n{dest}")
        except OSError as exc:
            self.log("Could not save file: " + str(exc))

    def _copy(self, value):
        if value:
            self.root.clipboard_clear()
            self.root.clipboard_append(value)
            self.log("Copied to clipboard.")

    def start_game_bridge(self):
        try:
            self.bridge = ThreadingHTTPServer(("127.0.0.1", BRIDGE_PORT), GameBridgeHandler)
            self.bridge.launcher = self
            threading.Thread(target=self.bridge.serve_forever, daemon=True).start()
            self.bridge_status.set(f"Game bridge ready: 127.0.0.1:{BRIDGE_PORT}")
            self.log("LOCAL GAME BRIDGE READY on 127.0.0.1:51246")
        except OSError as exc:
            self.bridge_status.set("Game bridge unavailable — see log")
            self.log(f"GAME BRIDGE ERROR: {exc} (check if another launcher is running)")

    def open_mod_folder(self):
        base = Path.home() / 'Documents' / 'Electronic Arts' / 'The Sims 4' / 'Mods'
        if not base.exists():
            base = Path(os.environ.get('USERPROFILE', str(Path.home()))) / 'OneDrive' / 'Documents' / 'Electronic Arts' / 'The Sims 4' / 'Mods'
        if base.exists():
            os.startfile(str(base))
        else:
            messagebox.showinfo("Mods folder not found", "Open the active Sims 4 user folder manually (often Documents or OneDrive/Documents/Electronic Arts/The Sims 4/Mods).")

    def log(self, msg):
        self.log_box.configure(state="normal")
        self.log_box.insert("end",str(msg)+"\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def params(self):
        name = self.name.get().strip()
        if not 1 <= len(name) <= 24:
            raise ValueError("Player name must be 1–24 characters")
        port = int(self.port.get())
        if not 1 <= port <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        return name, port

    def host(self):
        if self.server or self.client:
            messagebox.showinfo("Already active","Disconnect before creating a new session")
            return
        try:
            name,port=self.params()
            self.server=LobbyServer(port=port,events=self.events)
            self.server.start()
            self.code.set(self.server.room_code)
            self.address.set("127.0.0.1")
            self.save_settings()
            self.log(f"HOST READY | share your LAN/ Hamachi IPv4 address + port {port} + code {self.server.room_code} privately.")
            self.log("Your friend should NOT use 127.0.0.1. They need your host PC's actual LAN address.")
            self._connect("127.0.0.1",port,self.server.room_code,name)
        except (ValueError,OSError) as exc:
            if self.server:
                self.server.close()
                self.server=None
            messagebox.showerror("Cannot host",str(exc))

    def join(self):
        if self.server or self.client:
            messagebox.showinfo("Already active","Disconnect before joining a new session")
            return
        try:
            name,port=self.params()
            host=self.address.get().strip()
            code=self.code.get().strip()
            if not host or not (len(code)==6 and code.isdigit()):
                raise ValueError("Enter the host's IPv4 address and 6-digit room code")
            self.save_settings()
            self._connect(host,port,code,name)
        except ValueError as exc:
            messagebox.showerror("Cannot join",str(exc))

    def _connect(self,host,port,code,name):
        self.client=LobbyClient(events=self.events)
        self.status.set("Connecting…")
        self.client.connect(host,port,code,name)

    def send(self):
        text=self.chat.get().strip()
        if text and self.client and self.client.connected:
            self.client.chat(text)
            self.chat.set("")
        elif text:
            self.log("Connect to the lobby before sending a message.")

    def disconnect(self):
        if self.client:
            self.client.close()
            self.client=None
        if self.server:
            self.server.close()
            self.server=None
        self.status.set("Not connected")
        self.players.set("No players connected")
        self.target_combo["values"] = []
        self.file_target.set("")
        self.incoming.clear()
        self.log("Session stopped.")

    def drain(self):
        try:
            while True:
                typ,value=self.events.get_nowait()
                if typ=="connected":
                    self.status.set("Connected to lobby")
                    self.log(value)
                elif typ=="players":
                    self.players.set("Players: "+", ".join(map(str,value)))
                    options = [str(v) for v in value if str(v) != self.name.get()]
                    self.target_combo["values"] = options
                    if self.file_target.get() not in options:
                        self.file_target.set(options[0] if options else "")
                elif typ=="file_offer":
                    self.offer_file(value)
                elif typ=="file_chunk":
                    self.receive_chunk(value)
                elif typ=="file_done":
                    self.finish_file(value)
                elif typ=="transfer_status":
                    self.log("FILE: " + str(value))
                elif typ=="transfer_reply":
                    self.log("FILE: " + str(value.get("from", "Player")) +
                             (" accepted." if value.get("type") == "file_accept" else " declined."))
                elif typ=="chat":
                    self.log(f"{value[0]}: {value[1]}")
                elif typ=="game":
                    self.log("GAME: " + value)
                    self.bridge_status.set("Game bridge: in-game ping received!")
                    if self.client and self.client.connected:
                        self.client.chat("[Stage 2] In-game handshake confirmed on this PC")
                elif typ=="disconnected":
                    self.status.set("Disconnected")
                    self.players.set("No players connected")
                    self.log(value)
                else:
                    self.log(f"{typ.upper()}: {value}")
        except queue.Empty:
            pass
        self.root.after(100,self.drain)

    def browse(self):
        path=filedialog.askopenfilename(title="Find TS4_x64.exe",filetypes=[("Executables","*.exe"),("All files","*.*")])
        if path:
            self.exe.set(path)
            self.save_settings()

    def launch_game(self):
        path=self.exe.get().strip().strip('"')
        if not (Path(path).is_file() and path.lower().endswith(".exe")):
            messagebox.showwarning("Choose game","Select your installed TS4_x64.exe with Browse.")
            return
        try:
            subprocess.Popen([path],cwd=str(Path(path).parent))
            self.log("Requested Sims 4 launch. NOTE: game state is NOT shared in this build.")
        except OSError as exc:
            messagebox.showerror("Launch failed",str(exc))

    def quit(self):
        self.save_settings()
        if self.bridge:
            self.bridge.shutdown()
            self.bridge.server_close()
        self.disconnect()
        self.root.destroy()

if __name__=="__main__":
    app=tk.Tk()
    Launcher(app)
    app.mainloop()
