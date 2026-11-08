<#
  Try the installer on a clean, throwaway Windows: Windows Sandbox opens with dist\ mapped
  read-only and starts the newest setup EXE. Everything inside disappears when it closes.

    .\installer\test-in-sandbox.ps1

  Needs Windows 10/11 Pro with the "Windows Sandbox" feature turned on
  (Settings > System > Optional features > More Windows features > Windows Sandbox).
#>
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$dist = Join-Path $root "dist"
$setup = Get-ChildItem $dist -Filter "MBS-Studio-Setup-*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $setup) { throw "No installer in dist\. Build it first: .\installer\build.ps1" }
$sandboxExe = Join-Path $env:WINDIR "System32\WindowsSandbox.exe"
if (-not (Test-Path $sandboxExe)) {
    throw "Windows Sandbox is not turned on. Enable it under: More Windows features > Windows Sandbox, then restart."
}

$config = @"
<Configuration>
  <Networking>Enable</Networking>
  <vGPU>Enable</vGPU>
  <MemoryInMB>8192</MemoryInMB>
  <MappedFolders>
    <MappedFolder>
      <HostFolder>$dist</HostFolder>
      <SandboxFolder>C:\Setup</SandboxFolder>
      <ReadOnly>true</ReadOnly>
    </MappedFolder>
  </MappedFolders>
  <LogonCommand>
    <Command>C:\Setup\$($setup.Name)</Command>
  </LogonCommand>
</Configuration>
"@
$wsb = Join-Path $env:TEMP "mbs-studio-sandbox.wsb"
Set-Content -Path $wsb -Value $config -Encoding UTF8
Write-Host "Opening Windows Sandbox with $($setup.Name)..."
Start-Process $wsb
