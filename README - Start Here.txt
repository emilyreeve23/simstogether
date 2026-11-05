SIMS TOGETHER v0.4 — SIMPLE LAUNCHER + TEST FILE SENDING
========================================================

This is a LAN/Hamachi prototype; it is NOT live Sims 4 multiplayer.

REQUIREMENTS
- Windows with Python 3 and Tkinter to run the launcher.
- Your existing v0.2 SimsTogether_Stage2.ts4script in each Sims 4 Mods folder
  for the optional in-game st.ping test (no need to rebuild the mod).
- Both players MUST use v0.4 of the launcher. The network protocol differs
  from v0.1/v0.2/v0.3 so old and new launchers cannot connect to each other.

HOW TO START
1. Extract the entire ZIP on both computers. Don't open the BAT in the ZIP.
2. Double-click 'Start Sims Together.bat' on each computer.
3. Host: enter a name, use port 51245, click Host, and share your newly
   generated room code and your actual LAN/Hamachi IPv4 address privately.
   The host field changes to 127.0.0.1 because this is its own local connection;
   the friend must NOT enter 127.0.0.1.
4. Friend: enter a different name, host's IPv4, port 51245 and room code;
   click Join. If using Hamachi, both PCs need to be in the same Hamachi network.
5. Both should see two names in the session and can exchange chat messages.

TEST THE NEW FILE FEATURE
1. Make a small text file on one PC. For example, write "Hello from my PC" in
   Notepad and save it as hello.txt.
2. In the launcher, select the OTHER player's name in the 'To' dropdown.
3. Click 'Choose file...' and select your hello.txt file. Confirm sending.
4. The other player gets an Accept/Decline prompt. They must click Yes.
5. Accepted files arrive in:
   %USERPROFILE%\Downloads\SimsTogether Received Files
6. Open the file ONLY if you trust its sender; do not execute unknown files.

SAFETY / LIMITATIONS
- Maximum file size: 2 MB, one manual transfer at a time recommended.
- Incoming files need approval, use a separate download folder, and are checked
  against the sender's claimed SHA-256 hash. Existing files are not overwritten.
- No automatic writes to game saves or Mods. This feature is NOT a save sync.
- No encryption or strong authentication. Use only on trusted private LAN/
  Hamachi networks, with Windows firewall limited to private networks.
- Do not port-forward the host port or expose it directly to the internet.
- On Windows, avoid trying to receive executable/script files: test with .txt.
- Old v0.3 launcher cannot connect to v0.4 because we added transfer messages.

IN-GAME PING FROM STAGE 2 (UNCHANGED)
- Keep SimsTogether_Stage2.ts4script in Mods and Script Mods Allowed enabled.
- Start the launcher, connect to the lobby, then launch Sims 4 normally.
- Load a household and type st.ping into the game cheat console (Ctrl+Shift+C).
- The local launcher should show the in-game message.
- This build does not synchronise Sims, saves, mods, or DLC.

If the lobby works on your home LAN but not Hamachi, first check both PCs show
connected in Hamachi, use the HOST'S Hamachi IPv4, keep the port at 51245, and
ensure Windows firewall permits Python on Private networks.
