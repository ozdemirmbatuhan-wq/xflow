param(
    [Parameter(Mandatory = $true)][string]$Flow5Source,
    [Parameter(Mandatory = $true)][string]$OccRoot,
    [Parameter(Mandatory = $true)][string]$GmshRoot,
    [Parameter(Mandatory = $true)][string]$OpenBlasRoot
)

$ErrorActionPreference = "Stop"

function To-QMakePath([string]$Path) {
    return (Resolve-Path $Path).Path.Replace("\", "/")
}

$occInclude = To-QMakePath (Join-Path $OccRoot "inc")
$occLib = To-QMakePath (Join-Path $OccRoot "win64\vc14\lib")
$gmshInclude = To-QMakePath (Join-Path $GmshRoot "include")
$gmshLib = To-QMakePath (Join-Path $GmshRoot "lib")
$openBlasInclude = To-QMakePath (Join-Path $OpenBlasRoot "include")
$openBlasLib = To-QMakePath (Join-Path $OpenBlasRoot "lib")

$projects = @(
    (Join-Path $Flow5Source "flow5-lib\flow5-lib.pro"),
    (Join-Path $Flow5Source "flow5-io-lib\flow5-io-lib.pro")
)

foreach ($project in $projects) {
    $text = Get-Content -Raw -Path $project
    if ($project.EndsWith("flow5-lib.pro")) {
        $mklStart = $text.IndexOf("#----------------------- MKL  ---------------------")
        if ($mklStart -lt 0) {
            throw "flow5 7.57 MKL block was not found in $project"
        }
        $occStart = $text.IndexOf("#------------ OPEN CASCADE", $mklStart)
        if ($occStart -le $mklStart) {
            throw "flow5 7.57 MKL block was not found in $project"
        }
        $openBlasBlock = @"
#----------------------- OpenBLAS ---------------------
    DEFINES += OPENBLAS
    INCLUDEPATH += `"$openBlasInclude`"
    LIBS += -L`"$openBlasLib`"
    LIBS += -lopenblas


"@
        $text = $text.Substring(0, $mklStart) + $openBlasBlock + $text.Substring($occStart)
    }
    $text = $text.Replace("INCLUDEPATH += D:/bin/build/OCCT/inc", "INCLUDEPATH += `"$occInclude`"")
    $text = $text.Replace("LIBS += -LD:/bin/build/OCCT/win64/vc14/lib", "LIBS += -L`"$occLib`"")
    $text = $text.Replace("INCLUDEPATH += D:/bin/gmsh-4.14.1-Windows64-sdk/include/", "INCLUDEPATH += `"$gmshInclude`"")
    $text = $text.Replace("LIBS += -L`"D:/bin/gmsh-4.14.1-Windows64-sdk/lib`"", "LIBS += -L`"$gmshLib`"")
    if ($text.Contains("D:/bin/build/OCCT") -or $text.Contains("D:/bin/gmsh-4.14.1")) {
        throw "flow5 dependency paths could not be patched in $project"
    }
    Set-Content -Path $project -Value $text -Encoding utf8
}

Write-Host "flow5 qmake projects configured for OCCT, Gmsh and OpenBLAS"
