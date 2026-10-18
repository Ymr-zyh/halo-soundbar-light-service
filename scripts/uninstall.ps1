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
    if ([string]::IsNullOrWhiteSpace($env:ProgramFiles) -or -not [IO.Path]::IsPathRooted($env:ProgramFiles)) {
        throw 'ProgramFiles must be an absolute directory.'
    }
    $ServiceName = 'HaloSoundbarLightService'
    $InstallDirectory = [IO.Path]::GetFullPath((Join-Path $env:ProgramFiles 'HaloSoundbarLightService'))
    $InstalledExe = Join-Path $InstallDirectory 'HaloSoundbarLightService.exe'
    $ExpectedDirectory = [IO.Path]::GetFullPath($env:ProgramFiles)
    if ([IO.Path]::GetDirectoryName($InstallDirectory) -ne $ExpectedDirectory) {
        throw 'Installation directory is outside ProgramFiles.'
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
    Remove-Item -LiteralPath $InstallDirectory -Recurse -Force
    Write-Output 'Service and installation directory removed.'
}
catch {
    Write-Error -Message $_ -ErrorAction Continue
    exit 1
}
