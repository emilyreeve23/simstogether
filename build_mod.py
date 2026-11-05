"""Compile .ts4script with the game-matching Python 3.7 interpreter.
Usage: py -3.7 build_mod.py
"""
import os
import py_compile
import sys
import zipfile
from pathlib import Path

base = Path(__file__).resolve().parent
if sys.version_info[:2] != (3, 7):
    raise SystemExit('This test mod requires Python 3.7 for bytecode compatibility. Your version: %s' % sys.version)
source = base / 'sims_together_stage2.py'
compiled = base / 'sims_together_stage2.pyc'
py_compile.compile(str(source), cfile=str(compiled), doraise=True)
output = base / 'SimsTogether_Stage2.ts4script'
with zipfile.ZipFile(str(output), 'w', zipfile.ZIP_DEFLATED) as archive:
    archive.write(str(compiled), 'sims_together_stage2.pyc')
compiled.unlink()
print('Created:', output)
print('Copy this file into the active Sims 4 Mods folder on BOTH PCs.')
