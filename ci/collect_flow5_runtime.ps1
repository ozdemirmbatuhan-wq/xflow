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

# AeroOpt is distributed as a portable ZIP, so collect the redistributable x64
# MSVC DLLs from the active toolchain and place only the runtime files that the
# dependency graph actually needs beside the runner.
$msvcRedistCandidates = @()
if (-not [string]::IsNullOrWhiteSpace($env:VCToolsRedistDir)) {
    $msvcRedistCandidates += (Join-Path $env:VCToolsRedistDir "x64")
}

$msvcRedistBases = @()
if (-not [string]::IsNullOrWhiteSpace($env:VCINSTALLDIR)) {
    $msvcRedistBases += (Join-Path $env:VCINSTALLDIR "Redist\MSVC")
}

$programFilesX86 = [Environment]::GetFolderPath("ProgramFilesX86")
$vswhere = Join-Path $programFilesX86 "Microsoft Visual Studio\Installer\vswhere.exe"
if (Test-Path $vswhere) {
    $visualStudioRoot = (& $vswhere -latest -products * `
        -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
        -property installationPath | Select-Object -First 1)
    if (-not [string]::IsNullOrWhiteSpace($visualStudioRoot)) {
        $msvcRedistBases += (Join-Path $visualStudioRoot "VC\Redist\MSVC")
    }
}

foreach ($base in ($msvcRedistBases | Select-Object -Unique)) {
    if (-not (Test-Path $base)) { continue }
    $crtFiles = @(
        Get-ChildItem -Path $base -Recurse -File -Filter "msvcp140.dll" -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '(?i)[\\/]x64[\\/]Microsoft\.VC\d+\.CRT[\\/]' } |
            Sort-Object -Property FullName -Descending
    )
    foreach ($crtFile in $crtFiles) {
        $msvcRedistCandidates += $crtFile.Directory.Parent.FullName
    }
}

$msvcRedistRoots = @(
    $msvcRedistCandidates |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) -and (Test-Path $_) } |
        ForEach-Object { (Resolve-Path $_).Path } |
        Select-Object -Unique
)
if ($msvcRedistRoots.Count -eq 0) {
    throw "The x64 Visual C++ redistributable DLL directory could not be found"
}

$msvcp140 = Get-ChildItem -Path $msvcRedistRoots -Recurse -File `
    -Filter "msvcp140.dll" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $msvcp140) {
    throw "MSVCP140.dll was not found below the x64 Visual C++ redistributable directory"
}
$msvcpHeaders = (& $dumpbin /nologo /headers $msvcp140.FullName 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0 -or $msvcpHeaders -notmatch '(?i)8664 machine \(x64\)') {
    throw "The located MSVCP140.dll is not a valid x64 runtime DLL: $($msvcp140.FullName)"
}

# The flow5 runner is a headless QCoreApplication and links only Qt6Core. It
# does not need GUI platform plugins, translations or QML deployment. Index the
# active Qt bin directory directly so the same dependency-closure pass can
# collect Qt6Core.dll and its MSVC runtime dependencies without windeployqt.
$qmake = (Get-Command qmake -ErrorAction Stop).Source
$qtBin = Split-Path -Parent $qmake
$qtCore = Join-Path $qtBin "Qt6Core.dll"
if (-not (Test-Path $qtCore)) {
    throw "Qt6Core.dll was not found beside qmake: $qtCore"
}

$searchRoots = @(
    (Join-Path $Flow5Source "XFoil-lib"),
    (Join-Path $Flow5Source "flow5-lib"),
    (Join-Path $Flow5Source "flow5-io-lib"),
    (Join-Path $OccRoot "win64\vc14\bin"),
    (Join-Path $GmshRoot "lib"),
    (Join-Path $OpenBlasRoot "bin"),
    $qtBin,
    $ThirdPartyRoot
) + @(
    $msvcRedistRoots
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

function Copy-DependencyClosure([string[]]$Roots) {
    $queue = [System.Collections.Generic.Queue[string]]::new()
    $queued = @{}

    foreach ($rootBinary in $Roots) {
        if (-not (Test-Path $rootBinary)) {
            throw "Dependency scan root was not found: $rootBinary"
        }
        $resolvedBinary = (Resolve-Path $rootBinary).Path
        $queueKey = $resolvedBinary.ToLowerInvariant()
        if (-not $queued.ContainsKey($queueKey)) {
            $queue.Enqueue($resolvedBinary)
            $queued[$queueKey] = $true
        }
    }

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

            $sourceKey = $dependency.ToLowerInvariant()
            if (-not $dllIndex.ContainsKey($sourceKey)) {
                throw "No packaged SDK provides runtime dependency $dependency (required by $binary)"
            }
            Copy-Item -Force $dllIndex[$sourceKey] $destinationDependency
            Write-Host "Collected runtime dependency $dependency"
            $queue.Enqueue($destinationDependency)
            $queued[$destinationDependency.ToLowerInvariant()] = $true
        }
    }
}

$runnerDestination = Join-Path $Destination "aeropt-flow5-runner.exe"
Copy-Item -Force $Runner $runnerDestination
Copy-DependencyClosure -Roots @($runnerDestination)

# Validate the complete packaged dependency graph. Only Windows system DLLs
# may remain outside the destination.
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
    "TKernel.dll",
    "MSVCP140.dll",
    "VCRUNTIME140.dll"
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
