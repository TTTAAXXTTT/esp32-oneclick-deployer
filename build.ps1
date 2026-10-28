#Requires -Version 5.1
<#
.SYNOPSIS
Build the Windows x64 portable application and a ZIP. Never contacts a board.
.EXAMPLE
.\build.ps1 -Python ..\work\flash-venv\Scripts\python.exe
.NOTES
Install requirements.txt separately before building. This script does not
download packages or firmware, install drivers, or open serial ports.
#>
[CmdletBinding()]
param(
    [string]$Python,
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Build this application on Windows x64.'
}
$projectRoot = [IO.Path]::GetFullPath($PSScriptRoot)
if (-not $Python) {
    $localPython = Join-Path (Split-Path $projectRoot -Parent) 'work\flash-venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $localPython -PathType Leaf) {
        $Python = $localPython
    } else {
        $pythonCommand = Get-Command python.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1
        $Python = $pythonCommand.Source
    }
}
$pythonCommand = Get-Command $Python -CommandType Application -ErrorAction Stop | Select-Object -First 1
$pythonExe = $pythonCommand.Source

if (-not $OutputDirectory) { $OutputDirectory = Join-Path $projectRoot 'dist' }
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
$buildRoot = Join-Path $projectRoot 'build'
$bundleRoot = Join-Path $outputRoot 'ESP32-OneClick'

foreach ($relative in @('main.py', 'deployer_core.py', 'updater.py', 'deployer.spec', 'requirements.txt',
                        'tools\arduino-cli.exe', 'tools\manifest.json',
                        'README.md', 'firmware\agent-n16r8.bin', 'firmware\manifest.json')) {
    $requiredPath = Join-Path $projectRoot $relative
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required input is missing: $requiredPath"
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'licenses') -PathType Container)) {
    throw 'Add third-party licenses to licenses/ before building.'
}
$sourceInputs = @('source', 'firmware-source') | Where-Object {
    Test-Path -LiteralPath (Join-Path $projectRoot $_) -PathType Container
}
if (@($sourceInputs).Count -eq 0) {
    throw 'Add corresponding firmware source and build instructions under source/ or firmware-source/ before building.'
}

$environmentCheck = @'
import sys, struct, tkinter, importlib.metadata as m
assert struct.calcsize('P') == 8, 'Use x64 Python'
assert sys.version_info >= (3, 10), 'Python >=3.10 required'
assert m.version('esptool') == '5.1.0', 'esptool 5.1.0 required'
assert m.version('pyserial') == '3.5', 'pyserial 3.5 required'
import PyInstaller
t = tkinter.Tcl()
print('Build environment:', sys.version.split()[0], 'PyInstaller', PyInstaller.__version__, 'Tcl', t.eval('info patchlevel'))
'@
& $pythonExe -c $environmentCheck
if ($LASTEXITCODE -ne 0) { throw 'Build dependencies or Tcl are missing. See requirements.txt and README.md.' }

& $pythonExe -m PyInstaller --noconfirm --clean --distpath $outputRoot --workpath $buildRoot (Join-Path $projectRoot 'deployer.spec')
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

# The runtime data remains under _internal/. Keep human-readable documentation
# and preferred source alongside the executable without duplicating firmware.
Copy-Item -LiteralPath (Join-Path $projectRoot 'README.md') -Destination (Join-Path $bundleRoot 'README.md') -Force
foreach ($documentation in @('SOURCE-BUILD.md', 'docs')) {
    $documentationPath = Join-Path $projectRoot $documentation
    if (Test-Path -LiteralPath $documentationPath) {
        Copy-Item -LiteralPath $documentationPath -Destination $bundleRoot -Recurse -Force
    }
}
foreach ($sourceName in $sourceInputs) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $sourceName) -Destination $bundleRoot -Recurse -Force
}
$deployerSource = Join-Path $bundleRoot 'source\deployer'
$null = New-Item -ItemType Directory -Path $deployerSource -Force
foreach ($sourceFile in @('main.py', 'deployer_core.py', 'updater.py', 'deployer.spec', 'requirements.txt', 'build.ps1', 'README.md')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $sourceFile) -Destination (Join-Path $deployerSource $sourceFile) -Force
}
$testSource = Join-Path $projectRoot 'tests'
if (Test-Path -LiteralPath $testSource -PathType Container) {
    $testDestination = Join-Path $deployerSource 'tests'
    $null = New-Item -ItemType Directory -Path $testDestination -Force
    Get-ChildItem -LiteralPath $testSource -File -Filter '*.py' | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $testDestination $_.Name) -Force
    }
}

$buildStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$zipPath = Join-Path $outputRoot "ESP32-OneClick-Windows-x64-$buildStamp.zip"
Compress-Archive -LiteralPath $bundleRoot -DestinationPath $zipPath -CompressionLevel Optimal
$zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
$hashPath = "$zipPath.sha256.txt"
[IO.File]::WriteAllText($hashPath, "$zipHash  $([IO.Path]::GetFileName($zipPath))`n", [Text.UTF8Encoding]::new($false))
Write-Host "Portable folder: $bundleRoot"
Write-Host "ZIP: $zipPath"
Write-Host "SHA256: $zipHash"
