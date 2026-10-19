"""Package the independent v1.1.0 source and migrate verified portable bits.

The portable application is unchanged. Its original archive is pinned by SHA-256.
Only the source package is regenerated with this repository's layout and docs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'ESP32-OneClick-Source-v1.1.0/'
BASE_URL = 'https://github.com/TTTAAXXTTT/esp32-iphone-agent/releases/download/windows-deployer-v1.1.0/'
SOURCE_NAME = 'ESP32-OneClick-Source-v1.1.0.zip'
PORTABLE_NAME = 'ESP32-OneClick-N16R8-Windows-x64-v1.1.0.zip'
SOURCE_SHA = 'ca700f414df84ed8ffffdb1326a8af966a79fa63eba028af1e18c26ac6cbabb5'
PORTABLE_SHA = '2974797e26f4981e61d8054d3741486e9b84ad23cc86ace22e8987d9deb72721'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def obtain(path, filename, expected, cache):
    if path is None:
        path = cache / filename
        with urllib.request.urlopen(BASE_URL + filename, timeout=120) as response, path.open('wb') as output:
            shutil.copyfileobj(response, output)
    if digest(path) != expected:
        raise ValueError('Archive hash mismatch: ' + filename)
    return path


def make_source(source, output):
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    files = {name: (ROOT / name).read_bytes() for name in tracked if name and name != 'initial-project.zip'}
    manifests = [
        ('firmware/agent-n16r8.bin', json.loads((ROOT / 'firmware/manifest.json').read_text('utf-8-sig'))),
        ('tools/arduino-cli.exe', json.loads((ROOT / 'tools/manifest.json').read_text('utf-8-sig'))['arduino_cli']),
    ]
    with zipfile.ZipFile(source) as original:
        for name, manifest in manifests:
            data = original.read(PREFIX + name)
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
    parser.add_argument('--input-source', type=Path)
    parser.add_argument('--input-portable', type=Path)
    parser.add_argument('--output-dir', type=Path, default=ROOT.parent / 'publish-assets')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='esp32-release-') as temporary:
        cache = Path(temporary)
        source = obtain(args.input_source, SOURCE_NAME, SOURCE_SHA, cache)
        portable = obtain(args.input_portable, PORTABLE_NAME, PORTABLE_SHA, cache)
        make_source(source, args.output_dir / SOURCE_NAME)
        shutil.copyfile(portable, args.output_dir / PORTABLE_NAME)
    sums = ''.join(f'{digest(args.output_dir / name)}  {name}\n' for name in (PORTABLE_NAME, SOURCE_NAME))
    (args.output_dir / 'SHA256SUMS.txt').write_text(sums, 'utf-8', newline='\n')
    print(sums)


if __name__ == '__main__':
    main()
