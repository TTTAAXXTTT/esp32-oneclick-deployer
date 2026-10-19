# Arduino CLI 1.5.1

The bundled `tools/arduino-cli.exe` is the unmodified official Windows AMD64
Arduino CLI 1.5.1 release. Arduino SA distributes the main program under GNU
GPL version 3. The original license and dependency notices are in
`_internal/licenses/arduino-cli/` in the portable application.

The corresponding upstream source is supplied in `arduino-cli-1.5.1.tar.gz`.
This archive includes `go.mod`, `go.sum`, the original build scripts,
`LICENSE.txt`, and upstream `.licenses/` dependency license records.

## Provenance and integrity

- Release: <https://github.com/arduino/arduino-cli/releases/tag/v1.5.1>
- Binary archive: <https://downloads.arduino.cc/arduino-cli/arduino-cli_1.5.1_Windows_64bit.zip>
- Official checksums: <https://downloads.arduino.cc/arduino-cli/1.5.1-checksums.txt>
- Source: <https://codeload.github.com/arduino/arduino-cli/tar.gz/refs/tags/v1.5.1>
- Release commit: `01f3d4f2b`
- Official build date: `2026-06-05T10:22:12Z`

SHA256 values:

```text
fabe42e0eb04d00e776a66178299ff95a46c623dbc260f997e58fd514853dd40  arduino-cli_1.5.1_Windows_64bit.zip
1017de89179c3167e6b8a38ca6cc4091fa69a3cf97aafa7810f01423003f5571  arduino-cli.exe
1deac1e8d8eff69ef1b0cb8c5d4be2a3a81224b103eaf3403b77ff470c7f4ec6  1.5.1-checksums.txt
262fbe874f62677d01eb15593144ca82bd6e7c2d0c00f82aa93949ab12924de6  arduino-cli-1.5.1.tar.gz
```

The ZIP was checked against the official checksum file downloaded over HTTPS;
the EXE was compared byte for byte with the checked ZIP entry. The checksum file
also matched the digest published by GitHub's official release API. The local
source archive digest records the exact HTTPS response included in this package.

## Build the CLI from source

Building this dependency is optional. Normal application use starts the supplied
official EXE. For a source build, extract the source archive, install Go 1.26.1
(the version declared by the included `go.mod`) and run from the extracted
`arduino-cli-1.5.1/` directory in PowerShell:

```powershell
$env:GOOS = 'windows'
$env:GOARCH = 'amd64'
go mod download
go build -trimpath -ldflags '-X github.com/arduino/arduino-cli/internal/version.versionString=1.5.1 -X github.com/arduino/arduino-cli/internal/version.commit=01f3d4f2b -X github.com/arduino/arduino-cli/internal/version.date=2026-06-05T10:22:12Z' -o arduino-cli.exe .
.\arduino-cli.exe version
```

`go mod download` obtains the exact dependency versions pinned by `go.mod` and
`go.sum`. The original `Taskfile.yml` and `DistTasks.yml` contain the upstream
build and release tasks. A local rebuild need not have the same executable hash
as the official release. If deliberately replacing the bundled EXE, verify the
new build and update `tools/manifest.json` in the application source before
rebuilding the complete application.

This dependency preparation only ran `arduino-cli version`. It did not install
any board packages, download a firmware toolchain, open a serial port or write
firmware.
