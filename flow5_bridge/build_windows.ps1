param(
    [Parameter(Mandatory=$true)][string]$Flow5Source,
    [Parameter(Mandatory=$true)][string]$Flow5Build,
    [Parameter(Mandatory=$true)][string]$QtRoot,
    [Parameter(Mandatory=$true)][string]$GmshRoot,
    [Parameter(Mandatory=$true)][string]$OccRoot,
    [Parameter(Mandatory=$true)][string]$OpenBlasRoot,
    [string]$BuildDir = "build"
)

$ErrorActionPreference = "Stop"
$BridgeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$qualifiedHeaders = Join-Path $OpenBlasRoot "include\openblas"
if (-not (Test-Path (Join-Path $qualifiedHeaders "cblas.h"))) {
    New-Item -ItemType Directory -Force $qualifiedHeaders | Out-Null
    Copy-Item (Join-Path $OpenBlasRoot "include\*.h") $qualifiedHeaders -Force
}

$required = @(
    (Join-Path $OccRoot "inc\Standard.hxx"),
    (Join-Path $OccRoot "win64\vc14\lib\TKernel.lib"),
    (Join-Path $OpenBlasRoot "include\openblas\cblas.h"),
    (Join-Path $OpenBlasRoot "include\openblas\lapack.h"),
    (Join-Path $OpenBlasRoot "lib\libopenblas.lib")
)
foreach ($path in $required) {
    if (-not (Test-Path $path)) { throw "Required SDK file was not found: $path" }
}

$cmakeArguments = @(
    "-S"
    $BridgeDir
    "-B"
    $BuildDir
    "-G"
    "Visual Studio 17 2022"
    "-A"
    "x64"
    "-DFLOW5_SOURCE_DIR=$Flow5Source"
    "-DFLOW5_BUILD_DIR=$Flow5Build"
    "-DCMAKE_PREFIX_PATH=$QtRoot"
    "-DGMSH_ROOT=$GmshRoot"
    "-DOCC_INCLUDE_DIR=$(Join-Path $OccRoot 'inc')"
    "-DOCC_LIBRARY_DIR=$(Join-Path $OccRoot 'win64\vc14\lib')"
    "-DOPENBLAS_ROOT=$OpenBlasRoot"
    "-DFLOW5_USES_MKL=OFF"
)
& cmake @cmakeArguments
if ($LASTEXITCODE -ne 0) { throw "CMake configuration failed" }

cmake --build $BuildDir --config Release --parallel 16
if ($LASTEXITCODE -ne 0) { throw "AeroOpt flow5 runner build failed" }

$runner = Join-Path (Resolve-Path $BuildDir) "Release\aeropt-flow5-runner.exe"
if (-not (Test-Path $runner)) { throw "Runner was not produced: $runner" }
Write-Host "Runner:" $runner
