[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

try {
    $ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    $VenvDirectory = Join-Path $ProjectRoot '.venv'
    $VenvPython = Join-Path $VenvDirectory 'Scripts\python.exe'
    $RequirementsPath = Join-Path $ProjectRoot 'requirements-build.txt'
    $SpecPath = Join-Path $ProjectRoot 'HaloSoundbarLightService.spec'
    $BuildDirectory = Join-Path $ProjectRoot 'build'
    $DistDirectory = Join-Path $ProjectRoot 'dist'
    $TestTempDirectory = Join-Path $BuildDirectory 'pytest'
    $ExecutablePath = Join-Path $DistDirectory 'HaloSoundbarLightService.exe'
    $ChecksumPath = "$ExecutablePath.sha256"

    # Refuse junctions/symlinks before creating a venv or allowing pytest cleanup.
    foreach ($Directory in @($VenvDirectory, $BuildDirectory, $DistDirectory, $TestTempDirectory)) {
        if (Test-Path -LiteralPath $Directory) {
            $Item = Get-Item -LiteralPath $Directory -Force
            if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Refusing a reparse point: $Directory"
            }
        }
    }

    & python -m venv $VenvDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Python venv creation failed.' }

    & $VenvPython -m pip install -r $RequirementsPath
    if ($LASTEXITCODE -ne 0) { throw 'Build dependency installation failed.' }

    Push-Location -LiteralPath $ProjectRoot
    try {
        # pytest 7 requires the explicit basetemp's parent to exist already.
        New-Item -ItemType Directory -Path $BuildDirectory -Force | Out-Null
        & $VenvPython -m pytest -v --basetemp $TestTempDirectory
        if ($LASTEXITCODE -ne 0) { throw 'Tests failed; packaging was not attempted.' }

        foreach ($DirectoryName in @('build', 'dist')) {
            $TargetPath = [IO.Path]::GetFullPath((Join-Path $ProjectRoot $DirectoryName))
            $ExpectedPath = Join-Path $ProjectRoot $DirectoryName
            if (($TargetPath -ne $ExpectedPath) -or
                -not $TargetPath.StartsWith($ProjectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
                ([IO.Path]::GetDirectoryName($TargetPath) -ne $ProjectRoot)) {
                throw "Cleanup target is outside the project: $TargetPath"
            }
            if (Test-Path -LiteralPath $TargetPath) {
                $TargetItem = Get-Item -LiteralPath $TargetPath -Force
                if (($TargetItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw "Refusing to remove a reparse point: $TargetPath"
                }
                Remove-Item -LiteralPath $TargetPath -Recurse -Force
            }
        }

        & $VenvPython -m PyInstaller --noconfirm --clean --workpath $BuildDirectory --distpath $DistDirectory $SpecPath
        if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
        if (-not (Test-Path -LiteralPath $ExecutablePath -PathType Leaf)) {
            throw 'PyInstaller returned success without producing the expected EXE.'
        }
        $Hash = (Get-FileHash -LiteralPath $ExecutablePath -Algorithm SHA256).Hash.ToLowerInvariant()
        Set-Content -LiteralPath $ChecksumPath -Value "$Hash  HaloSoundbarLightService.exe" -Encoding Ascii
        Write-Output "Build complete: $ExecutablePath"
        Write-Output "SHA-256: $ChecksumPath"
    }
    finally {
        Pop-Location
    }
}
catch {
    Write-Error -Message $_ -ErrorAction Continue
    exit 1
}
