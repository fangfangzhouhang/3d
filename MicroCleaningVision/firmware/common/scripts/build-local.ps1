param(
  [Parameter(Mandatory = $true)][string]$KeilRoot,
  [Parameter(Mandatory = $true)][string]$DevicePackRoot,
  [Parameter(Mandatory = $true)][string]$CmsisInclude,
  [Alias('Project')][ValidateSet('stage2-stepmotor', 'f103-stage1')][string]$ProjectName = 'stage2-stepmotor'
)

$ErrorActionPreference = 'Stop'
$stageRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$firmwareRoot = (Resolve-Path (Join-Path $stageRoot '..')).Path
$keilProjectRoot = (Resolve-Path (Join-Path $stageRoot 'keil')).Path
$KeilRoot = (Resolve-Path -LiteralPath $KeilRoot).Path
$DevicePackRoot = (Resolve-Path -LiteralPath $DevicePackRoot).Path
$CmsisInclude = (Resolve-Path -LiteralPath $CmsisInclude).Path
$uv4 = Join-Path $KeilRoot 'UV4\UV4.exe'
foreach ($required in @($uv4, (Join-Path $DevicePackRoot 'Device\Include\stm32f10x.h'), (Join-Path $CmsisInclude 'core_cm3.h'))) {
  if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Missing local build dependency: $required" }
}
$buildRoot = Join-Path $stageRoot ('.tools\keil\' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $buildRoot 'Objects'), (Join-Path $buildRoot 'Listings') -Force | Out-Null
$projectText = [System.IO.File]::ReadAllText((Join-Path $keilProjectRoot "$ProjectName.uvprojx"))
[xml]$project = $projectText
# Adapt a disposable build copy, never the tracked engineer's project or installed Keil settings.
foreach ($node in $project.SelectNodes('//Groups/Group/Files/File/FilePath')) {
  $path = $node.InnerText
  if ($path -match '^C:\\Keil_v5\\ARM\\PACK\\Keil\\STM32F1xx_DFP\\[^\\]+\\(.+)$') {
    $resolvedSource = Join-Path $DevicePackRoot $Matches[1]
  } elseif (-not [System.IO.Path]::IsPathRooted($path)) {
    $resolvedSource = [System.IO.Path]::GetFullPath((Join-Path $keilProjectRoot $path))
  } else { $resolvedSource = $path }
  if (-not (Test-Path -LiteralPath $resolvedSource -PathType Leaf)) { throw "Build source does not exist: $resolvedSource" }
  $projectText = $projectText.Replace(('<FilePath>' + [System.Security.SecurityElement]::Escape($path) + '</FilePath>'),
    ('<FilePath>' + [System.Security.SecurityElement]::Escape($resolvedSource) + '</FilePath>'))
}
$applicationIncludes = if ($ProjectName -eq 'stage2-stepmotor') {
  @((Join-Path $stageRoot 'board'), $stageRoot,
    (Join-Path $firmwareRoot 'pump'), (Join-Path $firmwareRoot 'motion'),
    (Join-Path $stageRoot 'safety'), (Join-Path $stageRoot 'serial'))
} else {
  @((Join-Path $stageRoot 'compat\stage1\driver_a'),
    (Join-Path $stageRoot 'compat\stage1\common'),
    (Join-Path $stageRoot 'compat\stage1\system_b'), (Join-Path $stageRoot 'safety'), (Join-Path $stageRoot 'serial'))
}
# 两份 HAL 的头文件同名，但接口不同，绝不能用一个 include 路径大杂烩。
$localIncludes = (@($applicationIncludes) + @(
  (Join-Path $DevicePackRoot 'Device\Include'), (Join-Path $DevicePackRoot 'Device\StdPeriph_Driver\inc'),
  $CmsisInclude
)) -join ';'
$replacements = @{
  IncludePath = $localIncludes
  PackID = 'Keil.STM32F1xx_DFP.' + (Split-Path $DevicePackRoot -Leaf)
  OutputDirectory = '.\Objects\'
  ListingPath = '.\Listings\'
}
foreach ($tag in $replacements.Keys) {
  if ($tag -eq 'IncludePath') { $original = $project.SelectSingleNode('//Cads/VariousControls/IncludePath').InnerText }
  else { $original = $project.SelectSingleNode("//TargetCommonOption/$tag").InnerText }
  $projectText = $projectText.Replace(("<$tag>" + [System.Security.SecurityElement]::Escape($original) + "</$tag>"),
    ("<$tag>" + [System.Security.SecurityElement]::Escape($replacements[$tag]) + "</$tag>"))
}
$localProject = Join-Path $buildRoot "$ProjectName-local.uvprojx"
$log = Join-Path $buildRoot 'build.log'
[System.IO.File]::WriteAllText($localProject, $projectText, [System.Text.UTF8Encoding]::new($false))
Write-Output "Local build copy: $localProject"
Write-Output "Device pack: $DevicePackRoot"
Write-Output "CMSIS include: $CmsisInclude"
$process = Start-Process -FilePath $uv4 -ArgumentList @('-r', ('"' + $localProject + '"'), '-j0', '-o', ('"' + $log + '"')) -WindowStyle Hidden -Wait -PassThru
if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log }
if ($process.ExitCode -ne 0) { throw "Keil build failed (exit $($process.ExitCode)); log: $log" }
Write-Output "Keil local rebuild passed; log: $log"
Write-Output 'Build only. No download/flash command was issued and the tracked project was not rewritten.'
