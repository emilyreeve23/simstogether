"""Safe, non-destructive local Sims 4 save handling for LAN experiment.

No game memory access, edits to existing saves, or automatic Mods/DLC changes.
"""
import hashlib
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

SAVE_RE = re.compile(r"Slot_([0-9a-fA-F]{8})\.save\Z", re.IGNORECASE)
MAX_SAVE_BYTES = 128 * 1024 * 1024
MOD_EXTENSIONS = {".package", ".ts4script"}
PACK_RE = re.compile(r"(?:EP|GP|SP|FP|KP)\d{2}\Z", re.IGNORECASE)


def sims_user_folder():
    """Find most likely *user data* folder; users may override it in the UI."""
    home = Path.home()
    options = [home / "Documents" / "Electronic Arts" / "The Sims 4",
               home / "OneDrive" / "Documents" / "Electronic Arts" / "The Sims 4"]
    existing = [p for p in options if p.is_dir()]
    if existing:
        return str(max(existing, key=lambda p: (p / "saves").exists()))
    return str(options[0])


def saves_in(folder):
    location = Path(folder) / 'saves'
    if not location.is_dir():
        return []
    return sorted((p for p in location.iterdir() if p.is_file() and SAVE_RE.fullmatch(p.name)),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def running_game():
    """Conservative guard on Windows; a failure to query never claims success."""
    if os.name != 'nt':
        return False
    try:
        result = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=8, text=True,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired):
        # If process status cannot be checked, fail closed.
        return True
    if result.returncode:
        return True
    names = ('ts4_x64.exe', 'ts4_dx9_x64.exe', 'ts4.exe')
    return any(('"' + exe + '"') in result.stdout.lower() for exe in names)


def copy_snapshot(path, directory):
    """Copy to a private snapshot before transfer so the source cannot change mid-send."""
    source = Path(path)
    if not SAVE_RE.fullmatch(source.name) or not source.is_file():
        raise ValueError('Select a normal Sims 4 Slot_XXXXXXXX.save file.')
    if running_game():
        raise ValueError('Close The Sims 4 completely before snapshotting a save.')
    if source.stat().st_size > MAX_SAVE_BYTES:
        raise ValueError('Save is over the 128 MB test limit.')
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    snapshot = folder / (str(int(time.time() * 1000)) + '_' + source.name)
    shutil.copy2(str(source), str(snapshot))
    if snapshot.stat().st_size > MAX_SAVE_BYTES:
        snapshot.unlink()
        raise ValueError('Save is over the 128 MB test limit.')
    return snapshot


def import_staged_save(staged, sims_folder, backups_folder):
    """Create a *new* slot; never overwrite an existing save or version file."""
    source = Path(staged)
    if not source.is_file() or not source.name.lower().endswith('.save'):
        raise ValueError('Choose a received .save file first.')
    if running_game():
        raise ValueError('Close The Sims 4 on this PC before importing a save.')
    if source.stat().st_size <= 0 or source.stat().st_size > MAX_SAVE_BYTES:
        raise ValueError('Invalid or oversized save.')
    destination = Path(sims_folder) / 'saves'
    if not destination.is_dir():
        raise ValueError('No existing Sims 4 saves folder found. Open the game once to create it.')
    present = []
    for p in destination.iterdir():
        match = SAVE_RE.fullmatch(p.name) if p.is_file() else None
        if match:
            present.append(int(match.group(1), 16))
    # Deliberately use an unused slot *above* current slots, preserving every original.
    candidate = max(0x100, max(present, default=0) + 1)
    while candidate <= 0xFFFFFFFE:
        target = destination / ('Slot_%08x.save' % candidate)
        if not target.exists() and not any(destination.glob(target.name + '.ver*')):
            break
        candidate += 1
    else:
        raise RuntimeError('No unused save slots available.')
    # Import copy retained in dedicated backups first.
    backup_dir = Path(backups_folder)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '_' + target.name)
    shutil.copy2(str(source), str(backup))
    if sha256_file(source) != sha256_file(backup):
        backup.unlink()
        raise IOError('Backup validation failed; no save imported.')
    try:
        with source.open('rb') as input_file, target.open('xb') as output_file:
            shutil.copyfileobj(input_file, output_file, 1024 * 1024)
        if sha256_file(source) != sha256_file(target):
            target.unlink(missing_ok=True)
            raise IOError('Imported file hash mismatch.')
    except Exception:
        # A failed import must not leave a corrupt partially written *new* slot.
        if target.exists():
            target.unlink()
        raise
    return target, backup


def game_version(user_folder):
    source = Path(user_folder) / 'GameVersion.txt'
    try:
        return source.read_text(encoding='utf-8-sig').strip()[:100] or 'unknown'
    except OSError:
        return 'unknown'


def installed_pack_ids(game_exe):
    """Best-effort install-directory scan; NOT proof DLC is enabled or owned."""
    if not game_exe:
        return None
    path = Path(game_exe)
    if not path.is_file():
        return None
    root_candidates = [path.parent, *list(path.parents)[:4]]
    scores = []
    for root in root_candidates:
        try:
            found = {p.name.upper() for p in root.iterdir() if p.is_dir() and PACK_RE.fullmatch(p.name)}
        except OSError:
            continue
        scores.append(found)
    return sorted(max(scores, key=len, default=set()))


def scan_content(user_folder, game_exe):
    mods_dir = Path(user_folder) / 'Mods'
    digest = hashlib.sha256()
    count = 0
    extras = 0
    if mods_dir.exists():
        files = sorted((p for p in mods_dir.rglob('*') if p.is_file() and p.suffix.lower() in MOD_EXTENSIONS),
                       key=lambda p: str(p.relative_to(mods_dir)).lower())
        for p in files:
            relative = str(p.relative_to(mods_dir)).replace('\\', '/').lower()
            digest.update(relative.encode('utf-8', 'replace'))
            digest.update(bytes.fromhex(sha256_file(p)))
            count += 1
            if p.name.lower() != 'simstogether_stage2.ts4script':
                extras += 1
    return {'game_version': game_version(user_folder), 'mods_count': count,
            'other_mods': extras, 'mods_digest': digest.hexdigest(),
            'packs': installed_pack_ids(game_exe)}


def compare_content(local, remote):
    notes = []
    a, b = local.get('game_version'), remote.get('game_version')
    if a == 'unknown' or b == 'unknown':
        notes.append('Game version unknown on at least one PC.')
    elif a == b:
        notes.append('Game version file: MATCH')
    else:
        notes.append('Game version file: DIFFERENT (%s vs %s)' % (a, b))
    if local.get('mods_digest') == remote.get('mods_digest'):
        notes.append('Mod files (name + hash): MATCH (%s files)' % local.get('mods_count'))
    else:
        notes.append('Mod files: DIFFERENT (local %s, peer %s)' %
                     (local.get('mods_count'), remote.get('mods_count')))
    local_packs, remote_packs = local.get('packs'), remote.get('packs')
    if local_packs is None or remote_packs is None:
        notes.append('Installed DLC list: UNKNOWN (choose the game executable on each PC).')
    elif set(local_packs) == set(remote_packs):
        notes.append('Detected installed pack folders: MATCH (%s).' % len(local_packs))
    else:
        notes.append('Detected installed pack folders: DIFFERENT (local %s, peer %s).' %
                     (len(local_packs), len(remote_packs)))
    notes.append('Scan does NOT enable/disable content or verify pack ownership.')
    return notes
