param(
    [Parameter(Mandatory = $true)][string]$Runner,
    [Parameter(Mandatory = $true)][string]$Flow5Source,
    [Parameter(Mandatory = $true)][string]$OccRoot,
    [Parameter(Mandatory = $true)][string]$ThirdPartyRoot,
    [Parameter(Mandatory = $true)][string]$GmshRoot,
    [Parameter(Mandatory = $true)][string]$OpenBlasRoot,
    [Parameter(Mandatory = $true)][string]$Destination
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$dumpbin = (Get-Command dumpbin -ErrorAction Stop).Source

function Get-ImportedDlls([string]$Binary) {
    $output = & $script:dumpbin /nologo /dependents $Binary 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "dumpbin could not inspect runtime dependencies for $Binary"
    }
    return @(
        $output | ForEach-Object {
            if ($_ -match '^\s*([A-Za-z0-9][A-Za-z0-9_.+-]*\.dll)\s*$') {
                $Matches[1]
            }
        } | Sort-Object -Unique
    )
}

function Is-WindowsSystemDll([string]$Name) {
    $lower = $Name.ToLowerInvariant()
    if ($lower -match '^(api-ms-win-|ext-ms-win-)') { return $true }
    if ($lower -match '^(msvcp|vcruntime|concrt|vcomp)\d+(?:_[a-z0-9]+)*\.dll$') { return $false }
    return (Test-Path (Join-Path $env:SystemRoot "System32\$Name"))
}

function Is-WinDeployQtDependency([string]$Name) {
    $lower = $Name.ToLowerInvariant()
    return (
        $lower -match '^qt6.*\.dll$' -or
        $lower -match '^(msvcp|vcruntime|concrt|vcomp)\d+(?:_[a-z0-9]+)*\.dll$'
    )
}

$searchRoots = @(
    (Join-Path $Flow5Source "XFoil-lib"),
    (Join-Path $Flow5Source "flow5-lib"),
    (Join-Path $Flow5Source "flow5-io-lib"),
    (Join-Path $OccRoot "win64\vc14\bin"),
    (Join-Path $GmshRoot "lib"),
    (Join-Path $OpenBlasRoot "bin"),
    $ThirdPartyRoot
)

# Index release candidates once. A debug path receives lower priority when
# an SDK contains release and debug DLLs with the same filename.
$dllIndex = @{}
foreach ($root in $searchRoots) {
    if (-not (Test-Path $root)) { continue }
    $sortProperties = @(
        @{ Expression = { if ($_.FullName -match '(?i)[\\/](debug|debug[^\\/]*)[\\/]') { 1 } else { 0 } } }
        "FullName"
    )
    $candidates = @(
        Get-ChildItem -Path $root -Recurse -File -Filter "*.dll" -ErrorAction SilentlyContinue |
            Sort-Object -Property $sortProperties
    )
    foreach ($candidate in $candidates) {
        $key = $candidate.Name.ToLowerInvariant()
        if (-not $dllIndex.ContainsKey($key)) {
            $dllIndex[$key] = $candidate.FullName
        }
    }
}

$runnerDestination = Join-Path $Destination "aeropt-flow5-runner.exe"
Copy-Item -Force $Runner $runnerDestination

$queue = [System.Collections.Generic.Queue[string]]::new()
$queued = @{}
$queue.Enqueue($runnerDestination)
$queued[$runnerDestination.ToLowerInvariant()] = $true

while ($queue.Count -gt 0) {
    $binary = $queue.Dequeue()
    foreach ($dependency in (Get-ImportedDlls $binary)) {
        $destinationDependency = Join-Path $Destination $dependency
        if (Test-Path $destinationDependency) {
            $queueKey = $destinationDependency.ToLowerInvariant()
            if (-not $queued.ContainsKey($queueKey)) {
                $queue.Enqueue($destinationDependency)
                $queued[$queueKey] = $true
            }
            continue
        }
        if (Is-WindowsSystemDll $dependency) { continue }
        if (Is-WinDeployQtDependency $dependency) { continue }

        $sourceKey = $dependency.ToLowerInvariant()
        if (-not $dllIndex.ContainsKey($sourceKey)) {
            throw "No packaged SDK provides runtime dependency $dependency (required by $binary)"
        }
        Copy-Item -Force $dllIndex[$sourceKey] $destinationDependency
        $queue.Enqueue($destinationDependency)
        $queued[$destinationDependency.ToLowerInvariant()] = $true
    }
}

$windeployqt = (Get-Command windeployqt).Source
& $windeployqt --release --no-translations --compiler-runtime --dir $Destination `
    $runnerDestination
if ($LASTEXITCODE -ne 0) { throw "windeployqt failed" }

# Validate the complete, packaged dependency graph. At this point Qt and the
# Visual C++ runtime must be present too; only Windows system DLLs may remain
# outside the destination.
$packagedDlls = @{}
Get-ChildItem -Path $Destination -Recurse -File -Filter "*.dll" | ForEach-Object {
    $packagedDlls[$_.Name.ToLowerInvariant()] = $_.FullName
}
$packagedBinaries = @($runnerDestination) + @($packagedDlls.Values)
foreach ($binary in $packagedBinaries) {
    foreach ($dependency in (Get-ImportedDlls $binary)) {
        if (Is-WindowsSystemDll $dependency) { continue }
        if (-not $packagedDlls.ContainsKey($dependency.ToLowerInvariant())) {
            throw "Packaged runtime is missing $dependency (required by $binary)"
        }
    }
}

$required = @(
    "aeropt-flow5-runner.exe",
    "Qt6Core.dll",
    "XFoil1.dll",
    "flow5-lib.dll",
    "flow5-io-lib.dll",
    "libopenblas.dll",
    "TKernel.dll"
)
foreach ($name in $required) {
    if (-not (Test-Path (Join-Path $Destination $name))) {
        throw "Runtime dependency collection missed $name"
    }
}

if (-not (Get-ChildItem -Path $Destination -File -Filter "gmsh*.dll")) {
    throw "Runtime dependency collection missed the Gmsh DLL"
}

Write-Host "Collected $((Get-ChildItem $Destination -File).Count) runtime files in $Destination"
