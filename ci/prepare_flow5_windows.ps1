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

function Set-RequiredText(
    [string]$Text,
    [string]$OldValue,
    [string]$NewValue,
    [string]$Project
) {
    if ($Text.Contains($OldValue)) {
        return $Text.Replace($OldValue, $NewValue)
    }
    if ($Text.Contains($NewValue)) {
        return $Text
    }
    throw "Expected flow5 7.57 dependency entry was not found in $Project`: $OldValue"
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
        $openBlasBlock = @"
#----------------------- OpenBLAS ---------------------
    DEFINES += OPENBLAS
    INCLUDEPATH += `"$openBlasInclude`"
    LIBS += -L`"$openBlasLib`"
    LIBS += -lopenblas


"@
        if (-not $text.Contains($openBlasBlock.TrimEnd())) {
            $mklStart = $text.IndexOf("#----------------------- MKL  ---------------------")
            if ($mklStart -lt 0) {
                throw "flow5 7.57 Windows MKL block was not found in $project"
            }
            $occStart = $text.IndexOf("#------------ OPEN CASCADE", $mklStart)
            if ($occStart -le $mklStart) {
                throw "flow5 7.57 Windows MKL block was not found in $project"
            }
            $text = $text.Substring(0, $mklStart) + $openBlasBlock + $text.Substring($occStart)
        }
    }

    $replacements = @(
        @{
            Old = "INCLUDEPATH += D:/bin/build/OCCT/inc"
            New = "INCLUDEPATH += `"$occInclude`""
        },
        @{
            Old = "LIBS += -LD:/bin/build/OCCT/win64/vc14/lib"
            New = "LIBS += -L`"$occLib`""
        },
        @{
            Old = "INCLUDEPATH += D:/bin/gmsh-4.14.1-Windows64-sdk/include/"
            New = "INCLUDEPATH += `"$gmshInclude`""
        },
        @{
            Old = "LIBS += -L`"D:/bin/gmsh-4.14.1-Windows64-sdk/lib`""
            New = "LIBS += -L`"$gmshLib`""
        }
    )

    foreach ($replacement in $replacements) {
        $text = Set-RequiredText $text $replacement.Old $replacement.New $project
    }

    foreach ($replacement in $replacements) {
        if (-not $text.Contains($replacement.New)) {
            throw "flow5 dependency entry could not be verified in $project`: $($replacement.New)"
        }
    }

    Set-Content -Path $project -Value $text -Encoding utf8
}

Write-Host "flow5 qmake projects configured for OCCT, Gmsh and OpenBLAS"
