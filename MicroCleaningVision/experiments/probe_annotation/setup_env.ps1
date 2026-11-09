param([string]$Environment = '.venv')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($Environment -notmatch '^\.venv[-a-zA-Z0-9_]*$') { throw 'Environment must be a local .venv name' }
$source = Get-Content -Encoding UTF8 -LiteralPath sources.json -Raw | ConvertFrom-Json
if (-not (Test-Path -LiteralPath "$Environment\Scripts\python.exe")) {
    py -3.12 -m venv $Environment
    if ($LASTEXITCODE) { throw 'venv failed' }
}
$python = Join-Path $PSScriptRoot "$Environment\Scripts\python.exe"
& $python -c "import sys; assert sys.version_info[:3] == (3,12,4), sys.version"
if ($LASTEXITCODE) { throw 'Use the recorded CPython 3.12.4 runtime' }
$env:PIP_CACHE_DIR = Join-Path $PSScriptRoot '.cache\pip'
$env:SAM2_BUILD_CUDA = '0'
& $python -m pip install pip==25.2 setuptools==80.9.0 wheel==0.45.1
if ($LASTEXITCODE) { throw 'bootstrap failed' }
& $python download_torch.py
if ($LASTEXITCODE) { throw 'official torch wheel download/hash check failed' }
& $python -m pip install --no-deps '.cache/torch-2.7.1+cu128-cp312-cp312-win_amd64.whl'
if ($LASTEXITCODE) { throw 'torch wheel install failed' }
& $python -m pip install -r requirements.txt
if ($LASTEXITCODE) { throw 'requirements failed' }
if (-not (Test-Path -LiteralPath 'vendor\sam2\.git')) {
    git clone $source.sam2_repository vendor/sam2
    if ($LASTEXITCODE) { throw 'clone failed' }
}
git -C vendor/sam2 checkout --detach $source.sam2_commit
if ($LASTEXITCODE) { throw 'source checkout failed' }
$actual = git -C vendor/sam2 rev-parse HEAD
if ($actual -ne $source.sam2_commit) { throw 'source commit mismatch' }
$dirty = git -C vendor/sam2 status --porcelain
if ($dirty) { throw 'SAM-2 source is modified; preserve it and use a clean checkout' }
& $python -m pip install --no-deps --no-build-isolation ./vendor/sam2
if ($LASTEXITCODE) { throw 'SAM-2 install failed' }
& $python -m pip check
if ($LASTEXITCODE) { throw 'dependency check failed' }
& $python download_weight.py
if ($LASTEXITCODE) { throw 'checkpoint download/validation failed' }

