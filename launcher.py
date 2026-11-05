"""Sims Together v0.5: LAN lobby plus manually approved save-copy prototype."""
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
from save_tools import (sims_user_folder, saves_in, copy_snapshot, import_staged_save,
                        scan_content, compare_content, running_game, MAX_SAVE_BYTES)

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
        self.reply({'ok': True, 'app': 'SimsTogether', 'version': '0.5',
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
        root.title("Sims Together | v0.5 save test")
        root.geometry("825x700")
        root.minsize(730, 610)
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
        self.save_target = tk.StringVar()
        self.sims_folder = tk.StringVar(value=self.settings.get('sims_folder', sims_user_folder()))
        self.selected_save = tk.StringVar()
        self.save_choices = {}
        self.received_save = None
        self.local_report = None
        self.remote_reports = {}
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
            SETTINGS.write_text(json.dumps({"name":self.name.get(),"address":self.address.get(),"port":self.port.get(),"exe":self.exe.get(), "sims_folder":self.sims_folder.get()}, indent=2), encoding="utf-8")
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
        ttk.Label(top, text="Shared save test  ·  v0.5", foreground="#667282").pack(side="right", pady=8)

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

        tabs = ttk.Notebook(body)
        tabs.pack(fill="both", expand=True)
        session_tab = ttk.Frame(tabs, padding=8)
        save_tab = ttk.Frame(tabs, padding=8)
        tabs.add(session_tab, text="Session and chat")
        tabs.add(save_tab, text="Shared save test")
        game = ttk.LabelFrame(session_tab, text="The Sims 4", padding=11)
        game.pack(fill="x", pady=(0, 10))
        ttk.Label(game, textvariable=self.bridge_status, foreground="#5d697b").pack(side="left")
        ttk.Button(game, text="Mods folder", command=self.open_mod_folder).pack(side="right", padx=(7, 0))
        ttk.Button(game, text="Launch game", command=self.launch_game).pack(side="right", padx=(7, 0))
        ttk.Button(game, text="Browse…", command=self.browse).pack(side="right")

        transfer = ttk.LabelFrame(session_tab, text="Send a test file", padding=11)
        transfer.pack(fill="x", pady=(0, 10))
        ttk.Label(transfer, text="Send a file up to 2 MB. Your friend must accept it.").pack(side="left")
        ttk.Button(transfer, text="Choose file…", command=self.send_file).pack(side="right")
        self.target_combo = ttk.Combobox(transfer, textvariable=self.file_target, width=16, state="readonly")
        self.target_combo.pack(side="right", padx=9)
        ttk.Label(transfer, text="To:").pack(side="right")

        logs = ttk.LabelFrame(session_tab, text="Session messages", padding=10)
        logs.pack(fill="both", expand=True)
        self.log_box = tk.Text(logs, wrap="word", height=8, state="disabled", bg="white", fg="#202a36",
                               font=("Consolas", 9), relief="solid", bd=1, padx=8, pady=7)
        self.log_box.pack(fill="both", expand=True)
        msgrow = ttk.Frame(logs)
        msgrow.pack(fill="x", pady=(8, 0))
        ttk.Entry(msgrow, textvariable=self.chat).pack(side="left", fill="x", expand=True, padx=(0, 7))
        ttk.Button(msgrow, text="Send message", command=self.send).pack(side="right")
        self.root.bind("<Return>", lambda e: self.send() if self.root.focus_get() and self.root.focus_get().winfo_class() == "TEntry" else None)
        ttk.Label(session_tab, text="Test build: saves can be copied, but live Sims gameplay is NOT synchronised.",
                  foreground="#737d89").pack(anchor="w", pady=(8, 0))
        self.build_save_tab(save_tab)
        self.log("Ready. Host or join on port 51245; use your Hamachi IP for remote friends.")
        self.log("In Sims 4, enter st.ping to test the game bridge.")

    def build_save_tab(self, parent):
        ttk.Label(parent, text="Copy a host save to your friend's PC, then import into a NEW slot.",
                  font=("Segoe UI Semibold", 10)).pack(anchor='w', pady=(1, 4))
        ttk.Label(parent, text="The Sims 4 must be CLOSED on both PCs for snapshots and imports. No live game sync.",
                  foreground="#9a4a26").pack(anchor='w', pady=(0, 13))

        folderbox = ttk.LabelFrame(parent, text="Sims 4 user-data folder (Documents / OneDrive)", padding=10)
        folderbox.pack(fill="x", pady=(0, 9))
        row = ttk.Frame(folderbox)
        row.pack(fill="x")
        ttk.Entry(row, textvariable=self.sims_folder).pack(side='left', fill='x', expand=True, padx=(0, 8))
        ttk.Button(row, text="Browse...", command=self.select_sims_folder).pack(side='right')

        sourcebox = ttk.LabelFrame(parent, text="Host: select a save to share", padding=10)
        sourcebox.pack(fill="x", pady=(0, 9))
        srcrow = ttk.Frame(sourcebox)
        srcrow.pack(fill="x")
        self.save_combo = ttk.Combobox(srcrow, textvariable=self.selected_save, state='readonly')
        self.save_combo.pack(side='left', fill='x', expand=True, padx=(0, 8))
        ttk.Button(srcrow, text="Refresh saves", command=self.refresh_saves).pack(side='right')
        sendrow = ttk.Frame(sourcebox)
        sendrow.pack(fill="x", pady=(9, 0))
        ttk.Label(sendrow, text="Send to:").pack(side='left')
        self.save_target_combo = ttk.Combobox(sendrow, textvariable=self.save_target, width=16, state='readonly')
        self.save_target_combo.pack(side='left', padx=(7, 12))
        ttk.Button(sendrow, text="Send save snapshot", command=self.send_save).pack(side='left')
        ttk.Label(sendrow, text="Max 128 MB", foreground="#697582").pack(side='right')

        receivebox = ttk.LabelFrame(parent, text="Guest: verified received save", padding=10)
        receivebox.pack(fill="x", pady=(0, 9))
        self.save_received_text = tk.StringVar(value="No save received in this session")
        ttk.Label(receivebox, textvariable=self.save_received_text, wraplength=720,
                  foreground="#536073").pack(anchor='w', pady=(0, 8))
        importrow = ttk.Frame(receivebox)
        importrow.pack(fill="x")
        ttk.Button(importrow, text="Import into NEW save slot", command=self.import_save).pack(side='left', padx=(0, 8))
        ttk.Button(importrow, text="Choose staged save...", command=self.choose_staged).pack(side='left', padx=(0, 8))
        ttk.Button(importrow, text="Open received folder", command=self.open_staging).pack(side='left')

        checkbox = ttk.LabelFrame(parent, text="Mods / DLC compatibility report (informational)", padding=10)
        checkbox.pack(fill="both", expand=True)
        ttk.Button(checkbox, text="Scan this PC and share report", command=self.scan_local).pack(anchor='w', pady=(0, 6))
        self.report_box = tk.Text(checkbox, height=7, wrap='word', font=('Consolas', 9),
                                  bg='white', fg='#202a36', relief='solid', bd=1, padx=8, pady=6)
        self.report_box.pack(fill="both", expand=True)
        self.report_box.insert('end', 'No scan yet. Installed pack folders are only an estimate.\n'
                             'This does not disable mods/DLC or verify installed pack ownership.\n'
                             'For a true base-game test, use a new household and keep custom mods out of the game.\n')
        self.report_box.configure(state='disabled')
        self.refresh_saves()

    def report(self, line):
        self.report_box.configure(state='normal')
        self.report_box.insert('end', str(line) + '\n')
        self.report_box.see('end')
        self.report_box.configure(state='disabled')

    def select_sims_folder(self):
        chosen = filedialog.askdirectory(title='Choose Documents/Electronic Arts/The Sims 4')
        if chosen:
            self.sims_folder.set(chosen)
            self.save_settings()
            self.refresh_saves()

    def refresh_saves(self):
        folder = Path(self.sims_folder.get())
        self.save_choices = {}
        if not (folder / 'saves').is_dir():
            self.save_combo['values'] = []
            self.selected_save.set('')
            return
        for path in saves_in(folder):
            label = '%s  |  %.1f MB  |  %s' % (path.name, path.stat().st_size / 1048576,
                    time.strftime('%d %b %Y %H:%M', time.localtime(path.stat().st_mtime)))
            self.save_choices[label] = path
        choices = list(self.save_choices)
        self.save_combo['values'] = choices
        self.selected_save.set(choices[0] if choices else '')

    def send_save(self):
        if not self.server or not self.client or not self.client.connected:
            messagebox.showwarning('Host only', 'Start a hosting session before sending a save.')
            return
        recipient = self.save_target.get().strip()
        path = self.save_choices.get(self.selected_save.get())
        if not recipient or not path:
            messagebox.showinfo('Choose player and save', 'Select a saved game and another connected player.')
            return
        if running_game():
            messagebox.showwarning('Game still running', 'Close The Sims 4 before copying a save.')
            return
        try:
            size = path.stat().st_size
        except OSError:
            messagebox.showerror('Save unavailable', 'The selected save no longer exists. Refresh saves.')
            return
        if size > MAX_SAVE_BYTES:
            messagebox.showwarning('Too large', 'The current save-transfer limit is 128 MB.')
            return
        if not messagebox.askyesno('Share a save', 'Send a COPY of %s to %s?\n\n'
                                   'For the base-game test, use a NEW household without DLC content and remove/disable OTHER mods.\n'
                                   'The recipient must accept. No existing saves are overwritten.' %
                                   (path.name, recipient)):
            return
        client = self.client
        def worker():
            try:
                snapshot = copy_snapshot(path, self.data_dir() / 'Sent Save Snapshots')
                self.events.put(('save_status', 'Created local snapshot ' + snapshot.name))
                client.transmit_save(snapshot, recipient)
            except (OSError, ValueError) as exc:
                self.events.put(('save_status', 'Cannot send save: ' + str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def data_dir(self):
        return SETTINGS.parent

    def staging_dir(self):
        return Path.home() / 'Downloads' / 'SimsTogether Received Saves'

    def offer_save(self, msg):
        sender = str(msg.get('from', '?'))
        name = str(msg.get('name', ''))
        size = msg.get('size')
        if running_game():
            self.client.decline_save(msg)
            self.report('Incoming save declined because The Sims 4 appears to be running.')
            return
        if not messagebox.askyesno('Incoming shared save',
                                   '%s wants to share a saved game:\n\n%s (%s bytes)\n\n'
                                   'Accept into a separate staging folder?\n'
                                   'This does NOT replace or load any existing save.' %
                                   (sender, name, str(size))):
            self.client.decline_save(msg)
            self.report('Declined save from ' + sender)
            return
        try:
            self.client.begin_save_receive(msg, self.staging_dir())
            self.report('Accepting ' + name + ' from ' + sender)
        except (OSError, ValueError) as exc:
            self.client.decline_save(msg)
            self.report('Save offer rejected: ' + str(exc))

    def choose_staged(self):
        chosen = filedialog.askopenfilename(title='Select received Sims 4 save',
                initialdir=str(self.staging_dir()), filetypes=[('Sims save', '*.save')])
        if chosen:
            self.received_save = Path(chosen)
            self.save_received_text.set(str(self.received_save))

    def open_staging(self):
        path = self.staging_dir()
        path.mkdir(parents=True, exist_ok=True)
        if os.name == 'nt':
            os.startfile(str(path))
        else:
            self.report(str(path))

    def import_save(self):
        if not self.received_save:
            messagebox.showinfo('No received save', 'Receive a save or choose a staged .save file first.')
            return
        source = Path(self.received_save)
        if not source.is_file() or self.staging_dir().resolve() not in source.resolve().parents:
            messagebox.showerror('Invalid location', 'The imported file must be in SimsTogether Received Saves.')
            return
        if running_game():
            messagebox.showwarning('Close game', 'Close The Sims 4 completely before importing the save.')
            return
        if not messagebox.askyesno('Import a separate save',
                       'This will copy the received save into a NEW, UNUSED slot inside your Sims 4 saves folder.\n\n'
                       'Existing saves will not be overwritten. A backup of the imported file will be kept.\n\nProceed?'):
            return
        try:
            target, backup = import_staged_save(source, self.sims_folder.get(),
                                                  self.data_dir() / 'Imported Save Backups')
            self.log('SAVE: Imported to new slot ' + target.name)
            self.report('Imported: ' + str(target))
            self.report('Backup: ' + str(backup))
            self.refresh_saves()
            messagebox.showinfo('Save imported', 'New save created:\n%s\n\n'
                                'Start The Sims 4 normally and use Load Game.\n'
                                'The games are NOT live-synchronised.' % target)
        except (OSError, ValueError, RuntimeError) as exc:
            messagebox.showerror('Import failed', str(exc))

    def scan_local(self):
        self.report('Scanning local content (may take time for large Mods folders)...')
        def worker():
            try:
                result = scan_content(self.sims_folder.get(), self.exe.get())
                self.events.put(('scan_complete', result))
            except (OSError, ValueError) as exc:
                self.events.put(('save_status', 'Content scan failed: ' + str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def scan_finished(self, result):
        self.local_report = result
        self.report('Local game version: ' + result.get('game_version', 'unknown'))
        self.report('Mods: %s files (%s others besides Sims Together).' %
                    (result.get('mods_count'), result.get('other_mods')))
        self.report('Detected installed DLC folders: ' +
                     (', '.join(result.get('packs')) if result.get('packs') is not None else 'unknown'))
        if self.client and self.client.connected:
            try:
                self.client.report_compat(result)
                self.report('Shared compatibility summary with other players.')
            except (OSError, ConnectionError) as exc:
                self.report('Could not share summary: ' + str(exc))
        for player, report in self.remote_reports.items():
            self.report('Compared to ' + player + ':')
            for note in compare_content(result, report):
                self.report('  ' + note)

    def read_peer_report(self, msg):
        sender = str(msg.get('from', ''))
        if not sender or sender == self.name.get():
            return
        report = msg.get('report')
        if not isinstance(report, dict):
            return
        self.remote_reports[sender] = report
        self.report('Received compatibility report from ' + sender)
        if self.local_report:
            for note in compare_content(self.local_report, report):
                self.report('  ' + note)
        else:
            self.report('Click Scan this PC to compare.')

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
        self.save_target_combo["values"] = []
        self.file_target.set("")
        self.save_target.set("")
        self.remote_reports.clear()
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
                    self.save_target_combo["values"] = options
                    if self.file_target.get() not in options:
                        self.file_target.set(options[0] if options else "")
                    if self.save_target.get() not in options:
                        self.save_target.set(options[0] if options else "")
                elif typ=="file_offer":
                    self.offer_file(value)
                elif typ=="save_offer":
                    self.offer_save(value)
                elif typ=="save_status":
                    self.log('SAVE: ' + str(value))
                    self.report(value)
                elif typ=="save_received":
                    self.received_save = Path(value['path'])
                    self.save_received_text.set(str(self.received_save))
                    self.log('SAVE: Verified and staged from %s (%s bytes)' % (value['from'], value['size']))
                    self.report('SHA-256 verified: ' + str(self.received_save))
                    messagebox.showinfo('Shared save received',
                            'Verified copy saved to:\n%s\n\nUse Import into NEW save slot after the game is closed.' %
                            str(self.received_save))
                elif typ=="save_verified":
                    self.log('SAVE: ' + str(value.get('from')) + ' confirmed matching SHA-256 checksum.')
                    self.report('Peer has verified the received save.')
                elif typ=="scan_complete":
                    self.scan_finished(value)
                elif typ=="compat_report":
                    self.read_peer_report(value)
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
