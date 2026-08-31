$ErrorActionPreference = 'Stop'

$stageRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$bootstrap = Join-Path $PSScriptRoot 'bootstrap-zig.ps1'
$zig = & $bootstrap
if (($null -ne $LASTEXITCODE) -and ($LASTEXITCODE -ne 0)) { exit $LASTEXITCODE }

$testSources = @(Get-ChildItem -Path (Join-Path $stageRoot 'tests') -Filter '*.c' -File |
  Sort-Object FullName |
  ForEach-Object FullName)
if ($testSources.Count -eq 0) {
  throw "No C test files found under $(Join-Path $stageRoot 'tests')"
}

$appSources = @(Get-ChildItem -Path (Join-Path $stageRoot 'app/src') -Filter '*.c' -File -ErrorAction SilentlyContinue |
  Sort-Object FullName |
  ForEach-Object FullName)

$exitCode = 0
foreach ($testSource in $testSources) {
  $testExe = Join-Path ([System.IO.Path]::GetTempPath()) ("f401re-stage1-$($testSource.BaseName).exe")
  & $zig cc -std=c11 -Wall -Wextra -Werror -DFW_PROTOCOL_TESTING "-I$(Join-Path $stageRoot 'app/include')" $appSources $testSource -o $testExe
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  & $testExe
  if ($LASTEXITCODE -ne 0) { $exitCode = $LASTEXITCODE }
}
exit $exitCode
