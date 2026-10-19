# Corresponding firmware source

The complete sketch is in `hid_bridge/`. The AirPlay subtree is GPL-3.0; its full
license and original notices are retained. The original project's other files
retain the MIT license in `../../_internal/licenses/UPSTREAM-MIT.txt` in the portable bundle.

Upstream: https://github.com/onehumanbeing/esp32-iphone-agent
Exact base commit: bae786f7b3d04d9a2feb4fe2ef398b3f169bdd02
Source: https://github.com/onehumanbeing/esp32-iphone-agent/tree/bae786f7b3d04d9a2feb4fe2ef398b3f169bdd02

Dependencies and their full original source:

- Arduino CLI 1.3.1: https://github.com/arduino/arduino-cli/tree/v1.3.1
- Espressif Arduino core 3.3.12: https://github.com/espressif/arduino-esp32/tree/3.3.12
- Board package index: https://espressif.github.io/arduino-esp32/package_esp32_index.json
- NimBLE-Arduino 2.5.1: https://github.com/h2zero/NimBLE-Arduino/tree/2.5.1

The firmware is unmodified from the recorded source. Its N16R8 build uses 16 MB
flash and OPI PSRAM, with the upstream conservative `default_8MB` partition table.
The exact build command on Linux (paths without spaces), from this folder, is:

```sh
arduino-cli core update-index --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core install esp32:esp32@3.3.12 --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli lib install 'NimBLE-Arduino@2.5.1'
arduino-cli compile \
  --fqbn 'esp32:esp32:esp32s3:USBMode=default,CDCOnBoot=cdc,FlashSize=16M,PSRAM=opi,PartitionScheme=default_8MB' \
  --build-property "compiler.cpp.extra_flags=-include $PWD/hid_bridge/nimble_cfg.h" \
  --build-property "compiler.c.extra_flags=-include $PWD/hid_bridge/nimble_cfg.h" \
  --build-path "$PWD/build-cache" --output-dir "$PWD/build" hid_bridge
```

The core's post-build hook creates `hid_bridge.ino.merged.bin`: bootloader at
0x0, partition table at 0x8000, boot_app0 at 0xe000, app at 0x10000, padded to
16 MB. This is the file bundled as `agent-n16r8.bin` and written at address 0x0.

The original `flash.sh` is retained for reference (its default target is the
upstream 8 MB board). `flash.ps1` is the supplied Windows N16R8 build/upload
wrapper. Neither is run by the GUI; its source is supplied in `source/deployer/`.

The deployed firmware's size, hash, origin and full FQBN are also recorded in
`_internal/firmware/manifest.json` in the portable binary bundle.
