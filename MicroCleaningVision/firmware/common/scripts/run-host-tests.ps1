param([string]$ZigPath, [switch]$SkipCompatibility, [string]$BaselineRoot, [string]$PythonPath)

$ErrorActionPreference = 'Stop'
$commonRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$firmwareRoot = (Resolve-Path (Join-Path $commonRoot '..')).Path
if ([string]::IsNullOrWhiteSpace($ZigPath)) {
  $zigCommand = Get-Command zig -ErrorAction SilentlyContinue
  if ($null -ne $zigCommand) { $ZigPath = $zigCommand.Source }
}
if ([string]::IsNullOrWhiteSpace($ZigPath) -or -not (Test-Path -LiteralPath $ZigPath -PathType Leaf)) {
  throw 'Supply -ZigPath with a local zig.exe. No tool downloads or hardware connections.'
}
$ZigPath = (Resolve-Path -LiteralPath $ZigPath).Path
& $ZigPath version
if ($LASTEXITCODE -ne 0) { throw 'Cannot execute the supplied Zig compiler.' }

$keilRoot = Join-Path $commonRoot 'keil'
foreach ($projectName in @('stage2-stepmotor', 'f103-stage1')) {
  [xml]$project = Get-Content -LiteralPath (Join-Path $keilRoot "$projectName.uvprojx") -Raw
  $files = @($project.SelectNodes('//Groups/Group/Files/File/FilePath') | ForEach-Object { $_.InnerText })
  $required = if ($projectName -eq 'stage2-stepmotor') {
    @('..\main.c', '..\board\stm32f103_hal.c', '..\board\stm32f10x_it.c',
      '..\..\motion\stepmotor.c', '..\..\pump\pump.c', '..\safety\control_core.c',
      '..\serial\mcv1_protocol.c')
  } else {
    @('..\compat\stage1\common\main.c', '..\safety\control_core.c', '..\serial\mcv1_protocol.c')
  }
  foreach ($path in $required) {
    if (@($files | Where-Object { $_ -eq $path }).Count -ne 1) {
      throw "Keil must include exactly once: $projectName / $path"
    }
  }
  foreach ($path in $files) {
    if (-not [System.IO.Path]::IsPathRooted($path) -and $path -notmatch 'Keil_v5') {
      if (-not (Test-Path -LiteralPath (Join-Path $keilRoot $path) -PathType Leaf)) {
        throw "Missing project source: $projectName / $path"
      }
    }
  }
  $includes = $project.SelectSingleNode('//Cads/VariousControls/IncludePath').InnerText -split ';'
  foreach ($path in $includes) {
    if ($path -match 'tests[\\/]support') { throw 'Fake headers must never enter production builds.' }
    if (-not [System.IO.Path]::IsPathRooted($path) -and $path -notmatch 'Keil_v5') {
      if (-not (Test-Path -LiteralPath (Join-Path $keilRoot $path) -PathType Container)) {
        throw "Missing project include: $projectName / $path"
      }
    }
  }
}

$buildRoot = Join-Path $commonRoot ('.tools\host-tests\' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
$supportRoot = Join-Path $commonRoot 'tests\support'
$sources = @(
  (Join-Path $firmwareRoot 'pump\pump.c'), (Join-Path $firmwareRoot 'motion\stepmotor.c'),
  (Join-Path $commonRoot 'board\stm32f10x_it.c'), (Join-Path $supportRoot 'fake_board.c'),
  (Join-Path $commonRoot 'safety\control_core.c'),
  (Join-Path $commonRoot 'serial\mcv1_protocol.c')
)
$compilerArgs = @('-std=c99', '-Wall', '-Wextra', '-Werror', '-O0',
  "-I$supportRoot", "-I$(Join-Path $commonRoot 'board')",
  "-I$(Join-Path $commonRoot 'safety')", "-I$(Join-Path $commonRoot 'serial')",
  "-I$(Join-Path $firmwareRoot 'motion')", "-I$(Join-Path $firmwareRoot 'pump')")
$previousZigGlobalCache = $env:ZIG_GLOBAL_CACHE_DIR
$previousZigLocalCache = $env:ZIG_LOCAL_CACHE_DIR
try {
  $env:ZIG_GLOBAL_CACHE_DIR = Join-Path $commonRoot '.tools\zig-global-cache'
  $env:ZIG_LOCAL_CACHE_DIR = Join-Path $buildRoot 'zig-cache'
  $tests = @('pump\tests\test_pump.c', 'motion\tests\test_stepmotor.c',
    'common\tests\test_command_flow.c', 'common\tests\test_interlocks.c')
  foreach ($relative in $tests) {
    $testSource = Join-Path $firmwareRoot $relative
    $name = [System.IO.Path]::GetFileNameWithoutExtension($testSource)
    $executable = Join-Path $buildRoot "$name.exe"
    & $ZigPath cc @compilerArgs @sources $testSource -o $executable
    if ($LASTEXITCODE -ne 0) { throw "C compilation failed: $name" }
    & $executable
    if ($LASTEXITCODE -ne 0) { throw "C assertions failed: $name" }
  }
  $armExecutable = Join-Path $buildRoot 'test_arm_required.exe'
  & $ZigPath cc @compilerArgs '-DFW_REQUIRE_ARM_BUTTON=1' @sources (Join-Path $commonRoot 'tests\test_arm_required.c') -o $armExecutable
  if ($LASTEXITCODE -ne 0) { throw 'ARM-required compilation failed.' }
  & $armExecutable
  if ($LASTEXITCODE -ne 0) { throw 'ARM-required assertions failed.' }

  if (-not [string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path
    $peerExecutable = Join-Path $buildRoot 'protocol_peer.exe'
    & $ZigPath cc @compilerArgs @sources (Join-Path $commonRoot 'tests\protocol_peer.c') -o $peerExecutable
    if ($LASTEXITCODE -ne 0) { throw 'Offline protocol peer compilation failed.' }
    $transcript = Join-Path $buildRoot 'host_contract_transcript.json'
    & $PythonPath (Join-Path $commonRoot 'scripts\check-host-contract.py') $peerExecutable > $transcript
    if ($LASTEXITCODE -ne 0) { throw 'Python encoder / C entry / Python reply parser contract failed.' }
    Write-Output "Python/C contract passed (stdin/stdout only); transcript: $transcript"
  }

  if (-not [string]::IsNullOrWhiteSpace($BaselineRoot)) {
    $oldRoot = Join-Path (Resolve-Path -LiteralPath $BaselineRoot).Path 'stage2-stepmotor'
    foreach ($name in @('test_pump', 'test_stepmotor', 'test_command_flow')) {
      $oldExe = Join-Path $buildRoot "baseline_$name.exe"
      & $ZigPath cc -std=c99 -Wall -Wextra -Werror -O0 "-I$(Join-Path $oldRoot 'tests\support')" "-I$(Join-Path $oldRoot 'driver')" "-I$(Join-Path $oldRoot 'pump')" (Join-Path $oldRoot 'pump\pump.c') (Join-Path $oldRoot 'driver\stepmotor.c') (Join-Path $oldRoot 'driver\stm32f10x_it.c') (Join-Path $oldRoot 'tests\support\fake_board.c') (Join-Path $oldRoot "tests\$name.c") -o $oldExe
      if ($LASTEXITCODE -ne 0) { throw "Baseline compilation failed: $name" }
      & $oldExe
      if ($LASTEXITCODE -ne 0) { throw "Baseline failed: $name" }
    }
    Write-Output 'Preserved pre-migration snapshot: 3/3 passed (includes existing uncommitted source).'
  }

  if (-not $SkipCompatibility) {
    & (Join-Path $commonRoot 'compat\stage1\scripts\run-host-tests.ps1') -ZigPath $ZigPath
    if ($LASTEXITCODE -ne 0) { throw 'Stage1 compatibility tests failed.' }
  }
} finally {
  $env:ZIG_GLOBAL_CACHE_DIR = $previousZigGlobalCache
  $env:ZIG_LOCAL_CACHE_DIR = $previousZigLocalCache
}
Write-Output 'Main firmware: 5/5 C executables passed. No camera, COM port, flash, motor or pump used.'
Write-Output "Generated executables (Git ignored): $buildRoot"
