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
$openBlasLibrary = To-QMakePath (Join-Path $OpenBlasRoot "lib\libopenblas.lib")

$requiredDependencies = @(
    (Join-Path $GmshRoot "include\gmsh.h_cwrap"),
    (Join-Path $GmshRoot "include\gmshc.h"),
    (Join-Path $GmshRoot "lib\gmsh.dll.lib"),
    (Join-Path $OpenBlasRoot "include\openblas\cblas.h"),
    (Join-Path $OpenBlasRoot "include\openblas\lapack.h"),
    (Join-Path $OpenBlasRoot "include\openblas\lapacke_config.h"),
    (Join-Path $OpenBlasRoot "include\openblas\lapacke_mangling.h"),
    (Join-Path $OpenBlasRoot "lib\libopenblas.lib"),
    (Join-Path $OpenBlasRoot "bin\libopenblas.dll")
)
foreach ($dependency in $requiredDependencies) {
    if (-not (Test-Path $dependency)) {
        throw "Required flow5 SDK file was not found: $dependency"
    }
}

# The official binary Gmsh SDK can be built with a C++ compiler ABI that is
# incompatible with MSVC. Its gmsh.h_cwrap header exposes the same C++ API as
# inline wrappers over the stable C ABI, whose symbols are present in
# gmsh.dll.lib. Patch the one native Gmsh include in pinned flow5 7.57 before
# compiling the library; otherwise the final link fails with unresolved
# gmsh::model::*, gmsh::logger::* and related C++ symbols.
$gmeshGlobals = Join-Path $Flow5Source "flow5-lib\api\gmesh_globals.h"
$gmeshText = Get-Content -Raw -Path $gmeshGlobals
$nativeGmshInclude = '#include <gmsh.h>'
$wrappedGmshInclude = '#include <gmsh.h_cwrap>'
$nativeGmshCount = ([regex]::Matches($gmeshText, [regex]::Escape($nativeGmshInclude))).Count
$wrappedGmshCount = ([regex]::Matches($gmeshText, [regex]::Escape($wrappedGmshInclude))).Count
if ($nativeGmshCount -eq 1 -and $wrappedGmshCount -eq 0) {
    $gmeshText = $gmeshText.Replace($nativeGmshInclude, $wrappedGmshInclude)
}
elseif ($nativeGmshCount -ne 0 -or $wrappedGmshCount -ne 1) {
    throw "Unexpected Gmsh include layout in pinned flow5 7.57: native=$nativeGmshCount, wrapper=$wrappedGmshCount"
}
Set-Content -Path $gmeshGlobals -Value $gmeshText -Encoding utf8

$projects = @(
    (Join-Path $Flow5Source "flow5-lib\flow5-lib.pro"),
    (Join-Path $Flow5Source "flow5-io-lib\flow5-io-lib.pro")
)

foreach ($project in $projects) {
    $text = Get-Content -Raw -Path $project
    if ($project.EndsWith("flow5-lib.pro")) {
        $openBlasBlock = @"
#----------------------- OpenBLAS ---------------------
    DEFINES += OPENBLAS HAVE_LAPACK_CONFIG_H LAPACK_COMPLEX_CPP
    INCLUDEPATH += `"$openBlasInclude`"
    LIBS += -L`"$openBlasLib`"
    LIBS += `"$openBlasLibrary`"
    QMAKE_CXXFLAGS += /MP


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

# flow5 7.57 contains blank #elif directives in an OpenBLAS-only branch.
# MSVC reaches those directives once lapack.h is available, so normalize them
# in the temporary pinned source checkout before compilation.
$panelAnalysis = Join-Path $Flow5Source "flow5-lib\analysis3d\panelanalysis.cpp"
$panelText = Get-Content -Raw -Path $panelAnalysis
$blankElifPattern = '(?m)^([^\S\r\n]*)#elif[^\S\r\n]*\r?$'
$blankElifCount = ([regex]::Matches($panelText, $blankElifPattern)).Count
if ($blankElifCount -notin @(0, 8)) {
    throw "Unexpected flow5 7.57 blank #elif count: $blankElifCount"
}
$panelText = [regex]::Replace($panelText, $blankElifPattern, '$1#else')
$panelText = [regex]::Replace(
    $panelText,
    '(?m)^([^\S\r\n]*)#elif[^\S\r\n]+(INTEL_MKL|ACCELERATE_NEW_LAPACK)[^\S\r\n]*\r?$',
    '$1#elif defined $2'
)

# The same upstream OpenBLAS branch has one malformed fallback call.  The
# official 0.3.34 header currently selects the strlen branch, but fixing the
# fallback keeps the pinned source valid if that header macro ever changes.
$badGetrsCall = 'sgetrs_(&trans, &n, &nrhs, m_aijf.data(), &lda, m_ipiv.data(), srhs.data(), &ldb, &info,);'
$goodGetrsCall = 'sgetrs_(&trans, &n, &nrhs, m_aijf.data(), &lda, m_ipiv.data(), srhs.data(), &ldb, &info);'
$badGetrsCount = ([regex]::Matches($panelText, [regex]::Escape($badGetrsCall))).Count
if ($badGetrsCount -notin @(0, 1)) {
    throw "Unexpected flow5 7.57 malformed sgetrs_ call count: $badGetrsCount"
}
$panelText = $panelText.Replace($badGetrsCall, $goodGetrsCall)
Set-Content -Path $panelAnalysis -Value $panelText -Encoding utf8

Write-Host "flow5 qmake projects configured for OCCT, Gmsh C-ABI wrapper and OpenBLAS/LAPACK"
