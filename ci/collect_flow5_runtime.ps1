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

function Copy-Dlls([string]$Root, [string[]]$Patterns, [string]$PathFilter = "") {
    if (-not (Test-Path $Root)) { return }
    foreach ($pattern in $Patterns) {
        Get-ChildItem -Path $Root -Recurse -File -Filter $pattern -ErrorAction SilentlyContinue |
            Where-Object { -not $PathFilter -or $_.FullName -match $PathFilter } |
            ForEach-Object { Copy-Item -Force $_.FullName $Destination }
    }
}

Copy-Item -Force $Runner (Join-Path $Destination "aeropt-flow5-runner.exe")
Copy-Dlls (Join-Path $Flow5Source "XFoil-lib") @("*.dll")
Copy-Dlls (Join-Path $Flow5Source "flow5-lib") @("*.dll")
Copy-Dlls (Join-Path $Flow5Source "flow5-io-lib") @("*.dll")
Copy-Dlls (Join-Path $OccRoot "win64\vc14\bin") @("*.dll")
Copy-Dlls $ThirdPartyRoot @("*.dll") "(win64|x64|64\\|64/)"
Copy-Dlls $GmshRoot @("*.dll")
Copy-Dlls $OpenBlasRoot @("*.dll")

$windeployqt = (Get-Command windeployqt).Source
& $windeployqt --release --no-translations --compiler-runtime --dir $Destination `
    (Join-Path $Destination "aeropt-flow5-runner.exe")
if ($LASTEXITCODE -ne 0) { throw "windeployqt failed" }

$required = @("aeropt-flow5-runner.exe", "Qt6Core.dll")
foreach ($name in $required) {
    if (-not (Test-Path (Join-Path $Destination $name))) {
        throw "Runtime dependency collection missed $name"
    }
}

Write-Host "Collected $((Get-ChildItem $Destination -File).Count) runtime files in $Destination"
