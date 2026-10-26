# Run on the CONNECTED preparation PC only.
# From the suite root:  powershell -NoProfile -ExecutionPolicy Bypass -File application\scripts\prepare_python.ps1
$ErrorActionPreference = 'Stop'
$Root   = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path   # ...\application
$PyVer  = '3.11.9'
$PyDir  = Join-Path $Root 'python'
$Site   = Join-Path $Root 'site-packages'
$Wheels = Join-Path $Root 'wheelhouse'
$Tmp    = Join-Path $Root '_download_tmp'
$Req    = Join-Path $Root 'requirements.in'
New-Item -ItemType Directory -Force -Path $PyDir, $Site, $Wheels, $Tmp | Out-Null

# 1. Embeddable Python
$zip = Join-Path $Tmp "python-$PyVer-embed-amd64.zip"
Invoke-WebRequest "https://www.python.org/ftp/python/$PyVer/python-$PyVer-embed-amd64.zip" -OutFile $zip
Expand-Archive $zip -DestinationPath $PyDir -Force

# 2. Make it use ..\site-packages and enable 'site' (paths are relative to this file)
Set-Content (Join-Path $PyDir 'python311._pth') -Encoding ascii -Value @('python311.zip', '.', '..\site-packages', 'import site')
$py = Join-Path $PyDir 'python.exe'

# 3. pip into site-packages
$getpip = Join-Path $Tmp 'get-pip.py'
Invoke-WebRequest 'https://bootstrap.pypa.io/get-pip.py' -OutFile $getpip
& $py $getpip --target $Site --no-warn-script-location
if ($LASTEXITCODE) { throw 'get-pip failed' }

# 4. Download every wheel (binary only) into the wheelhouse
& $py -m pip download -r $Req -d $Wheels --only-binary=:all: --no-warn-script-location
if ($LASTEXITCODE) { throw 'pip download failed' }
& $py -m pip download pip -d $Wheels
if ($LASTEXITCODE) { throw 'pip wheel download failed' }

# 5. Install ONLY from the wheelhouse (proves it is complete)
& $py -m pip install --no-index --find-links $Wheels --target $Site --upgrade -r $Req --no-warn-script-location
if ($LASTEXITCODE) { throw 'offline install failed: wheelhouse incomplete' }

# 6. Freeze the exact versions, copy the local Plotly bundle (no CDN)
& $py -m pip freeze --path $Site | Set-Content (Join-Path $Root 'requirements.lock.txt') -Encoding ascii
$vendor = Join-Path $Root 'shared\static\vendor'
New-Item -ItemType Directory -Force -Path $vendor | Out-Null
Copy-Item (Join-Path $Site 'plotly\package_data\plotly.min.js') $vendor -Force

Remove-Item $Tmp -Recurse -Force
Write-Host "Python ready: $py"