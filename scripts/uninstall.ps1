[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

try {
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $Principal = [Security.Principal.WindowsPrincipal]::new($Identity)
    if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this script in an elevated administrator PowerShell.'
    }
    $ServiceName = 'HaloSoundbarLightService'
    $KnownFolderProgramFiles = [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFiles)
    if ([string]::IsNullOrWhiteSpace($KnownFolderProgramFiles) -or -not [IO.Path]::IsPathRooted($KnownFolderProgramFiles)) {
        throw 'The Windows Program Files known folder is not an absolute directory.'
    }
    $ProgramFilesDirectory = [IO.Path]::GetFullPath($KnownFolderProgramFiles)
    if (-not (Test-Path -LiteralPath $ProgramFilesDirectory -PathType Container)) {
        throw 'The Windows Program Files known folder does not exist.'
    }
    $InstallDirectory = [IO.Path]::GetFullPath((Join-Path $ProgramFilesDirectory 'HaloSoundbarLightService'))
    $InstalledExe = Join-Path $InstallDirectory 'HaloSoundbarLightService.exe'
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals(
        [IO.Path]::GetDirectoryName($InstallDirectory), $ProgramFilesDirectory
    )) {
        throw 'Installation directory is outside the Windows Program Files known folder.'
    }
    foreach ($Target in @($InstallDirectory, $InstalledExe)) {
        if (Test-Path -LiteralPath $Target) {
            $Item = Get-Item -LiteralPath $Target -Force
            if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Refusing an installation reparse point: $Target"
            }
        }
    }
    if (-not (Test-Path -LiteralPath $InstalledExe -PathType Leaf)) {
        throw 'Installed EXE is missing; no files were removed.'
    }
    $UnexpectedEntries = @(Get-ChildItem -LiteralPath $InstallDirectory -Force | Where-Object {
        -not [StringComparer]::OrdinalIgnoreCase.Equals($_.FullName, $InstalledExe)
    })
    if ($UnexpectedEntries.Count -ne 0) {
        throw 'Installation directory contains unexpected entries; no files were removed.'
    }
    $ServiceRecord = Get-CimInstance -ClassName Win32_Service -Filter "Name='$ServiceName'" -ErrorAction Stop
    if ($null -eq $ServiceRecord) {
        throw 'Service registration is missing; installation files were preserved.'
    }
    $RegisteredImagePath = ([string]$ServiceRecord.PathName).Trim()
    $ExpectedImagePath = '"' + $InstalledExe + '"'
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals($RegisteredImagePath, $ExpectedImagePath)) {
        throw 'Registered service ImagePath does not match the fixed installed EXE; files were preserved.'
    }
    & $InstalledExe uninstall
    if ($LASTEXITCODE -ne 0) { throw 'Service removal failed; installation files were preserved.' }
    try {
        $RemainingService = Get-Service -Name $ServiceName -ErrorAction Stop
    }
    catch {
        if ($_.FullyQualifiedErrorId -notlike 'NoServiceFoundForGivenName*') { throw }
        $RemainingService = $null
    }
    if ($null -ne $RemainingService) {
        throw 'Service still exists; installation files were preserved.'
    }
    $Directory = Get-Item -LiteralPath $InstallDirectory -Force
    if (($Directory.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'Installation directory changed into a reparse point; files were preserved.'
    }
    $Executable = Get-Item -LiteralPath $InstalledExe -Force
    if (($Executable.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'Installed EXE changed into a reparse point; files were preserved.'
    }
    Remove-Item -LiteralPath $InstalledExe -Force
    Remove-Item -LiteralPath $InstallDirectory -Force
    Write-Output 'Service and installation directory removed.'
}
catch {
    Write-Error -Message $_ -ErrorAction Continue
    exit 1
}
