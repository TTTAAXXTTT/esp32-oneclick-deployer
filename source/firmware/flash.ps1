#Requires -Version 5.1
<#
.SYNOPSIS
Build the ESP32-S3 N16R8 firmware on Windows; upload only to an explicit COM port.
.DESCRIPTION
Requires Arduino CLI, esp32:esp32 3.3.12 and NimBLE-Arduino 2.5.1 already installed.
Nothing is downloaded or installed. Without -Port no serial device is contacted.
Uses TinyUSB, 16 MB flash, OPI PSRAM and the existing conservative 8 MB partition table.
For the iPhone, connect the board's native USB/OTG connector, not its USB-to-UART connector.
.EXAMPLE
.\hardware\flash.ps1
.EXAMPLE
.\hardware\flash.ps1 -Port COM7 -DebugSerial
#>
[CmdletBinding()]
param(
    [ValidatePattern('^COM[1-9][0-9]*$')]
    [string]$Port,
    [string]$ArduinoCli = 'arduino-cli',
    [switch]$DebugSerial
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'This script is for Windows. On macOS/Linux use hardware/flash.sh.'
}

# ProcessStartInfo avoids Windows PowerShell 5.1 stripping the embedded quotes in
# --build-property when the forced-include path contains spaces. No shell is used.
function ConvertTo-NativeArgument {
    param([AllowEmptyString()][string]$Value)
    $escaped = [regex]::Replace($Value, '(\\*)"', '${1}${1}\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '${1}${1}')
    return '"' + $escaped + '"'
}

function Invoke-Tool {
    param([string]$Executable, [string[]]$Arguments, [switch]$Capture)
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = $Executable
    $start.Arguments = ($Arguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' '
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $start
    try {
        $null = $process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $output = $stdout.GetAwaiter().GetResult()
        $errors = $stderr.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0) {
            throw "$([IO.Path]::GetFileName($Executable)) exited with code $($process.ExitCode).`n$output`n$errors"
        }
        if ($Capture) { return $output }
        if ($output) { Write-Host $output.TrimEnd() }
        if ($errors) { Write-Host $errors.TrimEnd() }
    }
    finally { $process.Dispose() }
}

$cliCommand = Get-Command -Name $ArduinoCli -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $cliCommand) {
    throw 'Arduino CLI is missing. Install it separately and add it to PATH, or pass -ArduinoCli C:\path\arduino-cli.exe.'
}
$cli = $cliCommand.Source
$cores = Invoke-Tool $cli @('core', 'list', '--format', 'json') -Capture | ConvertFrom-Json
$core = @($cores.platforms | Where-Object { $_.id -eq 'esp32:esp32' })
if ($core.Count -ne 1) { throw 'Required core esp32:esp32@3.3.12 is not installed. Install it separately.' }
# Arduino CLI releases have used both JSON field names.
$coreVersion = if ($core[0].PSObject.Properties['installed_version']) {
    $core[0].installed_version
} else { $core[0].installed }
if ($coreVersion -ne '3.3.12') {
    throw "Required core esp32:esp32@3.3.12; found $coreVersion. Change it separately before building."
}
$libraries = Invoke-Tool $cli @('lib', 'list', 'NimBLE-Arduino', '--format', 'json') -Capture | ConvertFrom-Json
$nimble = @($libraries.installed_libraries | Where-Object { $_.library.name -eq 'NimBLE-Arduino' })
if ($nimble.Count -ne 1 -or $nimble[0].library.version -ne '2.5.1') {
    throw 'Exactly one NimBLE-Arduino@2.5.1 installation is required. Install/fix the library separately.'
}

$sketch = Join-Path $PSScriptRoot 'hid_bridge'
$header = (Join-Path $sketch 'nimble_cfg.h').Replace('\', '/')
if (-not (Test-Path -LiteralPath $header -PathType Leaf)) { throw "Missing $header" }
$outputDirectory = Join-Path $PSScriptRoot 'build\windows-n16r8'
# UploadMode=default deliberately avoids automatic 1200-baud port switching.
$fqbn = 'esp32:esp32:esp32s3:USBMode=default,CDCOnBoot=cdc,UploadMode=default,UploadSpeed=115200,FlashMode=qio,FlashSize=16M,PSRAM=opi,PartitionScheme=default_8MB'
$flags = '-include "' + $header + '"'
if ($DebugSerial) { $flags += ' -DDEBUG_SERIAL=1' }
$buildArguments = @(
    'compile', '--fqbn', $fqbn,
    '--build-property', "compiler.cpp.extra_flags=$flags",
    '--build-property', "compiler.c.extra_flags=$flags",
    '--build-path', (Join-Path $outputDirectory 'cache'),
    '--output-dir', $outputDirectory
)
Write-Host 'Building ESP32-S3 N16R8 (TinyUSB, 16 MB flash, OPI PSRAM; 8 MB partition table)...'
Invoke-Tool $cli ($buildArguments + @('--clean', $sketch))
Write-Host "Firmware output: $outputDirectory"
if (-not $PSBoundParameters.ContainsKey('Port')) {
    Write-Host 'Build complete. No board was contacted. To upload, rerun with -Port COMn after checking the port.'
    return
}

$Port = $Port.ToUpperInvariant()
$bootHelp = 'If needed: hold BOOT, press/release RESET (or reconnect USB), then release BOOT. Check the resulting COM port in Device Manager and rerun with that explicit -Port. Close serial monitors first.'
if ([IO.Ports.SerialPort]::GetPortNames() -notcontains $Port) {
    throw "The explicitly selected port $Port does not exist. $bootHelp"
}

# Locate the very same esptool Arduino will use, without searching for arbitrary
# executables or installing Python. --show-properties does not compile or upload.
$propertyText = Invoke-Tool $cli ($buildArguments + @('--show-properties=expanded', $sketch)) -Capture
$properties = @{}
foreach ($line in ($propertyText -split '\r?\n')) {
    if ($line -match '^([^=]+)=(.*)$') { $properties[$matches[1]] = $matches[2] }
}
if (-not $properties['tools.esptool_py.path'] -or -not $properties['tools.esptool_py.cmd']) {
    throw 'Could not locate the installed core esptool from Arduino build properties. Upload was not started.'
}
$esptool = Join-Path $properties['tools.esptool_py.path'] $properties['tools.esptool_py.cmd']
if (-not (Test-Path -LiteralPath $esptool -PathType Leaf)) {
    throw "Core esptool is missing at $esptool. Upload was not started."
}

# A read-only flash-id probe verifies both the actual chip (esptool rejects a
# mismatched --chip) and 16 MB physical flash. Never guess or auto-select a port.
Write-Host "Checking ESP32-S3 identity and flash size on explicitly selected $Port..."
try {
    $identity = Invoke-Tool $esptool @(
        '--chip', 'esp32s3', '--port', $Port, '--baud', '115200',
        '--before', 'default-reset', '--after', 'no-reset', '--no-stub', 'flash-id'
    ) -Capture
} catch { throw "Device identification failed; upload was not started. $bootHelp`n$_" }
if ($identity -notmatch '\bESP32-S3\b' -or $identity -notmatch 'Detected flash size:\s*16\s*MB\b') {
    throw "Refusing upload: expected an ESP32-S3 with 16 MB flash (N16R8).`n$identity"
}
Write-Host $identity.TrimEnd()
Write-Host "Uploading the just-built firmware to $Port..."
try {
    Invoke-Tool $cli @('upload', '--fqbn', $fqbn, '--port', $Port, '--protocol', 'serial', '--input-dir', $outputDirectory, $sketch)
} catch { throw "Upload failed. $bootHelp`n$_" }
Write-Host 'Upload complete. Press RESET if the board remains in download mode; use its native USB/OTG connector with the iPhone.'
