SIMS TOGETHER v0.5  |  SHARED SAVE COPY PROTOTYPE
================================================

WHAT THIS BUILD DOES
- Keeps the successful Stage 2 LAN/Hamachi lobby, chat and st.ping bridge.
- Keeps accepted small-file transfer (up to 2 MB).
- NEW: sends one manual Sims 4 .save snapshot from the host, up to 128 MB.
- NEW: receiver approves and receives it outside the game's save folder.
- NEW: verifies SHA-256 hash before declaring receipt successful.
- NEW: receiver may *manually* import that verified copy into a new unused
  Slot_XXXXXXXX.save in their Sims 4 saves folder.
- NEW: scans local Mods (.package/.ts4script file hashes), game version text,
  and likely installed DLC folder IDs, then shares a small comparison report.

WHAT THIS BUILD DOES NOT DO
- Does NOT connect Sims visually inside the game or synchronize live gameplay.
- Does NOT merge two save files or sync changes made after the copy.
- Does NOT automatically disable or transfer DLC, mods, or packs.
- Does NOT certify that a save uses base-game-only content.
- Does NOT force both game installations onto the same update/version.

Both people need The Sims 4 installed legitimately on their own devices/accounts.

SETUP ON BOTH PCs
1. Keep your working v0.4 folder as a backup. Extract this ZIP to a new folder
   on EACH PC. Do not run the BAT while the files are still in the ZIP.
2. Double-click "Start Sims Together.bat". Requires working Python 3 + tkinter
   and Windows. If you already installed Python for v0.4, you are ready.
3. Keep your previously installed SimsTogether_Stage2.ts4script in the Mods folder
   if you want to test st.ping. YOU DO NOT NEED TO REBUILD IT for v0.5.
4. Host chooses a name and clicks Host. Share the host's LAN/Hamachi IPv4,
   port 51245 and NEW six-digit room code with the other player.
5. Guest enters host's actual LAN/Hamachi IPv4 (not 127.0.0.1), port 51245,
   room code, and clicks Join. Both need this v0.5 (protocol v3).
6. The game launch button is optional; you may start The Sims 4 as usual.

NEW: SHARED-SAVE COPY TEST (THE SIMS 4 MUST BE CLOSED ON BOTH PCs)
1. On the HOST PC, launch The Sims 4 normally once to create a NEW base-game
   TEST household and use Save As / Save, then quit The Sims 4 fully.
   For base-game testing, DO NOT build with DLC items or custom content.
2. In Sims Together v0.5, go to the "Shared save test" tab on BOTH PCs.
3. Check the Sims 4 user-data path. Usually:
      %USERPROFILE%\Documents\Electronic Arts\The Sims 4
   or %USERPROFILE%\OneDrive\Documents\Electronic Arts\The Sims 4
   Choose the actual folder with a "saves" subfolder using Browse.
4. On the HOST, click Refresh saves, select the desired Slot_XXXXXXXX.save,
   select the guest player in "Send to", then click Send save snapshot.
5. On the GUEST, accept the incoming offer. The launcher copies the bytes to:
      %USERPROFILE%\Downloads\SimsTogether Received Saves
   It compares the file size and SHA-256 checksum; no game save was changed.
6. The HOST should see a receipt confirmation (matching checksum).
7. On the GUEST, click "Import into NEW save slot" while The Sims 4 is CLOSED.
   The launcher uses a NEW slot ID and NEVER overwrites an existing save file.
   A backup of the imported copy is kept under:
      %APPDATA%\SimsTogetherPrototype\Imported Save Backups
8. Start The Sims 4 normally on the GUEST. Select Load Game and find the new
   save. Confirm the household/lot matches the host's original test save.
9. The HOST and GUEST can each load their own identical starting copy, but
   any play that follows is separate / NOT synchronised across computers.

NEW: COMPATIBILITY REPORT
- In the Shared save test tab, click "Scan this PC and share report" on both PCs.
- Compare the displayed game-version file, Mod-file digest, and installed
  pack-folder IDs. A difference is a warning, not a software restriction.
- This scans file/folder presence; the game itself may enable/disable packs
  differently. It does not grant DLC, copy purchased content, or verify licences.
- Mod hashes are computed locally. Only the summary digest/count is sent.
- It can take time with a very large Mods folder. The scan runs in a worker.
- To test base-game-only, use a NEW save and make sure you are not running
  gameplay-altering CC/mods or using pack-specific content. Keep only our ping
  test script as needed. Do not remove files while The Sims 4 is running.

SAFETY
- This build copies saves, it does not overwrite, delete, or merge existing
  saves. Previous saves remain in place. It keeps an imported-copy backup.
- ALWAYS quit the game before snapshot, transfer, or import. The program
  checks for common Sims 4 Windows processes and refuses if it sees one.
- The server is plaintext and the six-digit room code is not strong security.
  Only use trusted private LAN/Hamachi networks. NO INTERNET PORT FORWARDING.
- This build has no save deserialisation or game-state injection, so a shared
  file does NOT give you in-game multiplayer yet.
- Exchanging a save created with unavailable packs/mods may load with missing
  content or behave unexpectedly. Use a disposable base-game test household.
- The first-time test should not involve important saved games. Preserve your
  Sims folder backup independently. See EA's official backup instructions:
  https://help.ea.com/en/articles/the-sims/the-sims-4/backing-up-and-resetting-the-sims-4/

TROUBLESHOOTING
- No saves displayed: check the Documents vs OneDrive folder, and confirm the
  game has been saved. Only normal Slot_XXXXXXXX.save files are listed.
- Server bind WinError 10013: port 51245 worked previously; check it is not
  held/reserved, and close other Sims Together versions.
- Game bridge unavailable: close older v0.2/v0.3/v0.4 launcher instances;
  only one launcher can use local TCP port 51246.
- Save offered but can't import: quit the game; confirm the active user-data
  directory has a saves folder; keep the verified file in the staging folder.
- Can't see the imported save: verify that v0.5 was pointed to the Sims 4 user
  folder actively used by your current Windows profile and game installation.

NEXT MILESTONE
A separate game-state/interaction synchronisation engine must still be
built and independently tested. Save copying alone is not multiplayer.
