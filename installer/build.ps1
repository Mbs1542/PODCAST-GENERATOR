<#
  Build dist\MBS-Studio-Setup-<version>.exe.

  Unsigned (for testing):
    .\installer\build.ps1

  Signed (for release; Windows Smart App Control blocks unsigned installers):
    # certificate in the Windows certificate store or on a USB token
    .\installer\build.ps1 -SignCommand 'signtool.exe sign /fd sha256 /tr http://timestamp.digicert.com /td sha256 /a $f'

    # Azure Artifact Signing (formerly Trusted Signing)
    .\installer\build.ps1 -SignCommand 'signtool.exe sign /fd sha256 /tr http://timestamp.acs.microsoft.com /td sha256 /dlib C:\ArtifactSigning\Azure.CodeSigning.Dlib.dll /dmdf C:\ArtifactSigning\metadata.json $f'

  In the sign command, $f is the file Inno Setup signs (it signs the setup and the uninstaller).
  Use single quotes so PowerShell leaves $f alone.
#>
param([string]$SignCommand = "")

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
          "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 is not installed (winget install JRSoftware.InnoSetup)" }

$arguments = @("/Q")
if ($SignCommand) {
    $arguments += @("/DSIGN", "/Smbs=$SignCommand")
}
& $iscc @arguments (Join-Path $PSScriptRoot "MBS-Studio.iss")
if ($LASTEXITCODE -ne 0) { throw "ISCC failed (exit code $LASTEXITCODE)" }

$setup = Get-ChildItem (Join-Path $root "dist") -Filter "MBS-Studio-Setup-*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$signature = Get-AuthenticodeSignature $setup.FullName
Write-Host ("{0}  {1:N1} MB  signature: {2}" -f $setup.Name, ($setup.Length / 1MB), $signature.Status)
if ($SignCommand -and $signature.Status -ne "Valid") { throw "The installer is not validly signed: $($signature.StatusMessage)" }
if (-not $SignCommand) { Write-Host "Unsigned build: fine for testing, but Smart App Control will block it on machines where it is on." }
