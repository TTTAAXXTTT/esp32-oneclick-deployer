"""Fetch only the two build inputs; verify pinned manifests before writing."""
from pathlib import Path
import hashlib
import json
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
URL = 'https://github.com/TTTAAXXTTT/esp32-oneclick-deployer/releases/download/v1.1.1/ESP32-OneClick-Source-v1.1.1.zip'

def main():
    firmware = json.loads((ROOT / 'firmware/manifest.json').read_text('utf-8-sig'))
    cli = json.loads((ROOT / 'tools/manifest.json').read_text('utf-8-sig'))['arduino_cli']
    targets = [('firmware/agent-n16r8.bin', firmware), ('tools/arduino-cli.exe', cli)]
    def expected(meta):
        return meta.get('sha256') or meta.get('image_sha256')
    missing = [(name, meta) for name, meta in targets
               if not (ROOT / name).is_file() or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected(meta)]
    if not missing:
        print('Build inputs already verified.')
        return
    with tempfile.TemporaryDirectory(prefix='esp32-inputs-') as directory:
        archive = Path(directory) / 'source.zip'
        with urllib.request.urlopen(URL, timeout=120) as response, archive.open('wb') as output:
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 100 * 1024 * 1024:
                    raise ValueError('Unexpected archive size')
                output.write(chunk)
        with zipfile.ZipFile(archive) as source:
            checked = []
            for name, meta in missing:
                entry = source.getinfo('ESP32-OneClick-Source-v1.1.1/' + name)
                if entry.file_size > 64 * 1024 * 1024:
                    raise ValueError('Unexpected build input size')
                data = source.read(entry)
                if hashlib.sha256(data).hexdigest() != expected(meta):
                    raise ValueError('SHA-256 mismatch: ' + name)
                checked.append((name, data))
            for name, data in checked:
                target = ROOT / name
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_suffix(target.suffix + '.download')
                temporary.write_bytes(data)
                temporary.replace(target)
                print('Verified: ' + name)

if __name__ == '__main__':
    main()
