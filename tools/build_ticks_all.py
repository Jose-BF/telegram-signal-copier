"""Rebuild runtime_data/ticks_all/ (the tick root used by the history search) on this machine.

The cloud workspace used symlinks; here each file is hard-linked (no extra disk) or copied
from where it already lives in the repo (runtime_data/canal1_history_20260913/raw_mt5_v2 for
Jan-Sep and the week folders for 14-25 Sep). Every file is checked by size + md5 against
runtime_data/ticks_all_manifest/manifest.json, so the ticks are byte-identical to the ones
used for the search of 26-27/09.

Usage (from the repo root):  python tools/build_ticks_all.py [--copy]
"""
import hashlib, json, os, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
man = json.loads((ROOT / 'runtime_data/ticks_all_manifest/manifest.json').read_text())
dst_root = ROOT / 'runtime_data/ticks_all'
copy = '--copy' in sys.argv
bad = []
for rel, info in man.items():
    src, dst = ROOT / info['from'], dst_root / rel
    if not src.exists():
        bad.append(f'missing source {info["from"]}'); continue
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        try:
            if copy:
                raise OSError
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
    data = dst.read_bytes()
    if len(data) != info['size'] or hashlib.md5(data).hexdigest() != info['md5']:
        bad.append(f'checksum mismatch {rel}')
print(f'{len(man) - len(bad)} / {len(man)} files OK')
for b in bad[:20]:
    print('  ', b)
sys.exit(1 if bad else 0)
