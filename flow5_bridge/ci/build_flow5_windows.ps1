param(
    [Parameter(Mandatory = $true)][string]$Flow5Source,
    [Parameter(Mandatory = $true)][string]$OccRoot,
    [Parameter(Mandatory = $true)][string]$GmshRoot,
    [Parameter(Mandatory = $true)][string]$OpenBlasRoot,
    [Parameter(Mandatory = $true)][string]$BridgeBuild
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

$prepareScript = Join-Path $PSScriptRoot "prepare_flow5_windows.ps1"
$prepareArguments = @{
    Flow5Source = $Flow5Source
    OccRoot = $OccRoot
    GmshRoot = $GmshRoot
    OpenBlasRoot = $OpenBlasRoot
}
& $prepareScript @prepareArguments

$projects = @(
    @{ Directory = "XFoil-lib"; File = "XFoil-lib.pro" },
    @{ Directory = "flow5-lib"; File = "flow5-lib.pro" },
    @{ Directory = "flow5-io-lib"; File = "flow5-io-lib.pro" }
)

foreach ($project in $projects) {
    Push-Location (Join-Path $Flow5Source $project.Directory)
    try {
        & qmake $project.File -spec win32-msvc "CONFIG+=release"
        if ($LASTEXITCODE -ne 0) { throw "qmake failed for $($project.File)" }
        & nmake /NOLOGO
        if ($LASTEXITCODE -ne 0) { throw "nmake failed for $($project.File)" }
    }
    finally {
        Pop-Location
    }
}

$qmake = (Get-Command qmake).Source
$qtRoot = Split-Path -Parent (Split-Path -Parent $qmake)

$cmakeArguments = @(
    "-S"
    (Join-Path $repositoryRoot "flow5_bridge")
    "-B"
    $BridgeBuild
    "-G"
    "Visual Studio 17 2022"
    "-A"
    "x64"
    "-DFLOW5_SOURCE_DIR=$Flow5Source"
    "-DFLOW5_BUILD_DIR=$Flow5Source"
    "-DGMSH_ROOT=$GmshRoot"
    "-DOCC_INCLUDE_DIR=$(Join-Path $OccRoot 'inc')"
    "-DOCC_LIBRARY_DIR=$(Join-Path $OccRoot 'win64\vc14\lib')"
    "-DCMAKE_PREFIX_PATH=$qtRoot"
    "-DOPENBLAS_ROOT=$OpenBlasRoot"
    "-DFLOW5_USES_MKL=OFF"
    "-DFLOW5_VERSION=7.57"
)
& cmake @cmakeArguments
if ($LASTEXITCODE -ne 0) { throw "CMake configuration failed" }

cmake --build $BridgeBuild --config Release --parallel 16
if ($LASTEXITCODE -ne 0) { throw "AeroOpt flow5 runner build failed" }

$runner = Join-Path $BridgeBuild "Release\aeropt-flow5-runner.exe"
if (-not (Test-Path $runner)) { throw "Runner was not produced: $runner" }
Write-Host "Built $runner"
