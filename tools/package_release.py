"""Package this repository's complete source and a locally built portable ZIP.

Prepare build inputs with prepare-build-inputs.py, build with build.ps1, then run
python tools/package_release.py --input-portable PATH_TO_PORTABLE_ZIP.
No older repository or release is used by this packaging tool.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'ESP32-OneClick-Source-v1.1.0/'
SOURCE_NAME = 'ESP32-OneClick-Source-v1.1.0.zip'
PORTABLE_NAME = 'ESP32-OneClick-N16R8-Windows-x64-v1.1.0.zip'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def make_source(output):
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    files = {name: (ROOT / name).read_bytes() for name in tracked if name and name != 'initial-project.zip'}
    manifests = [
        ('firmware/agent-n16r8.bin', json.loads((ROOT / 'firmware/manifest.json').read_text('utf-8-sig'))),
        ('tools/arduino-cli.exe', json.loads((ROOT / 'tools/manifest.json').read_text('utf-8-sig'))['arduino_cli']),
    ]
    for name, manifest in manifests:
        data = (ROOT / name).read_bytes()
        if len(data) != manifest['size'] or hashlib.sha256(data).hexdigest() != manifest['sha256']:
            raise ValueError('Build input mismatch: ' + name)
        files[name] = data
    entries = [{'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
               for name, data in sorted(files.items())]
    files['SOURCE-INVENTORY.json'] = (json.dumps({'version': '1.1.0', 'files': entries}, indent=2,
                                               ensure_ascii=False) + '\n').encode()
    files['SHA256SUMS.source.txt'] = ''.join(f"{row['sha256']}  {row['path']}\n" for row in entries).encode()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(files.items()):
            item = zipfile.ZipInfo(PREFIX + name, date_time=(2026, 10, 19, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100644 << 16
            archive.writestr(item, data, compresslevel=9)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-portable', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, default=ROOT.parent / 'publish-assets')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.input_portable) as portable:
        if not any(name.endswith('/ESP32-OneClick.exe') for name in portable.namelist()):
            raise ValueError('Portable archive does not contain ESP32-OneClick.exe')
        if portable.testzip() is not None:
            raise ValueError('Portable archive CRC check failed')
    make_source(args.output_dir / SOURCE_NAME)
    destination = args.output_dir / PORTABLE_NAME
    if args.input_portable.resolve() != destination.resolve():
        shutil.copyfile(args.input_portable, destination)
    sums = ''.join(f'{digest(args.output_dir / name)}  {name}\n' for name in (PORTABLE_NAME, SOURCE_NAME))
    (args.output_dir / 'SHA256SUMS.txt').write_text(sums, 'utf-8', newline='\n')
    print(sums)


if __name__ == '__main__':
    main()
