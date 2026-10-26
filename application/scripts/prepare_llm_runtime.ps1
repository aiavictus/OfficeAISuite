# Run on the CONNECTED preparation PC only.
# powershell -NoProfile -ExecutionPolicy Bypass -File application\scripts\prepare_llm_runtime.ps1 [-Tag b1234]
param([string]$Tag = 'latest')
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Dest = Join-Path $Root 'llm_runtime\llama.cpp'
$Tmp  = Join-Path $Root '_download_tmp'
New-Item -ItemType Directory -Force -Path $Dest, $Tmp | Out-Null

$api = if ($Tag -eq 'latest') { 'https://api.github.com/repos/ggml-org/llama.cpp/releases/latest' }
       else { "https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/$Tag" }
$rel = Invoke-RestMethod $api -Headers @{ 'User-Agent' = 'OfflineAISuite' }
$asset = $rel.assets | Where-Object { $_.name -match '^llama-.*-bin-win-(cpu|avx2)-x64\.zip$' } | Select-Object -First 1
if (-not $asset) {
  $rel.assets | ForEach-Object { $_.name }
  throw 'No CPU x64 Windows asset found. Pick one from the list above and download it manually.'
}
$zip = Join-Path $Tmp $asset.name
Invoke-WebRequest $asset.browser_download_url -OutFile $zip
Expand-Archive $zip -DestinationPath $Dest -Force

$exe = Get-ChildItem $Dest -Recurse -Filter 'llama-server.exe' | Select-Object -First 1
if (-not $exe) { throw 'llama-server.exe not found after extraction' }
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash
Set-Content (Join-Path $Dest 'RUNTIME_VERSION.txt') -Encoding ascii -Value @($rel.tag_name, $asset.name, "SHA256=$hash", "exe=$($exe.FullName.Substring($Dest.Length+1))")
Remove-Item $Tmp -Recurse -Force
Write-Host "llama.cpp $($rel.tag_name) ready: $($exe.FullName)"