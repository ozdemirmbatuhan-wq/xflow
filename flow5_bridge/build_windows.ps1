param(
    [Parameter(Mandatory=$true)][string]$Flow5Source,
    [Parameter(Mandatory=$true)][string]$Flow5Build,
    [Parameter(Mandatory=$true)][string]$QtRoot,
    [Parameter(Mandatory=$true)][string]$GmshRoot,
    [string]$OccInclude = "",
    [string]$BuildDir = "build"
)

$ErrorActionPreference = "Stop"
$BridgeDir = Split-Path -Parent $MyInvocation.MyCommand.Path

cmake -S $BridgeDir -B $BuildDir -G "Visual Studio 17 2022" -A x64 `
    -DFLOW5_SOURCE_DIR="$Flow5Source" `
    -DFLOW5_BUILD_DIR="$Flow5Build" `
    -DCMAKE_PREFIX_PATH="$QtRoot" `
    -DGMSH_ROOT="$GmshRoot" `
    -DOCC_INCLUDE_DIR="$OccInclude"

cmake --build $BuildDir --config Release

Write-Host "Runner:" (Join-Path (Resolve-Path $BuildDir) "Release\aeropt-flow5-runner.exe")
