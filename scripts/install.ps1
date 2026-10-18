[CmdletBinding()]
param([string]$SourceExe = '')

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
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals(
        [IO.Path]::GetDirectoryName($InstallDirectory), $ProgramFilesDirectory
    )) {
        throw 'Installation directory is outside the Windows Program Files known folder.'
    }
    $InstalledExe = Join-Path $InstallDirectory 'HaloSoundbarLightService.exe'
    if ([string]::IsNullOrWhiteSpace($SourceExe)) {
        $SourceExe = Join-Path $PSScriptRoot '..\dist\HaloSoundbarLightService.exe'
    }
    $SourcePath = (Resolve-Path -LiteralPath $SourceExe).ProviderPath
    if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) {
        throw 'Source EXE was not found.'
    }
    try {
        $ExistingService = Get-Service -Name $ServiceName -ErrorAction Stop
    }
    catch {
        if ($_.FullyQualifiedErrorId -notlike 'NoServiceFoundForGivenName*') { throw }
        $ExistingService = $null
    }
    if ($null -ne $ExistingService) {
        throw 'Service already exists. Uninstall it before installing another build.'
    }
    foreach ($Target in @($InstallDirectory, $InstalledExe)) {
        if (Test-Path -LiteralPath $Target) {
            $Item = Get-Item -LiteralPath $Target -Force
            if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Refusing an installation reparse point: $Target"
            }
        }
    }
    New-Item -ItemType Directory -Path $InstallDirectory -Force | Out-Null
    Copy-Item -LiteralPath $SourcePath -Destination $InstalledExe -Force
    & $InstalledExe install
    if ($LASTEXITCODE -ne 0) { throw 'Service registration failed; service was not started.' }
    Start-Service -Name $ServiceName -ErrorAction Stop
    $Service = Get-Service -Name $ServiceName -ErrorAction Stop
    if ($Service.Status -ne 'Running') { throw 'Service did not reach Running state.' }
    Write-Output "Installed and started: $InstalledExe"
}
catch {
    Write-Error -Message $_ -ErrorAction Continue
    exit 1
}
