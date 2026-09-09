param(
  [string]$ToolsRoot,
  [string]$ArchivePath,
  [string]$ExpectedSha256 = '68659eb5f1e4eb1437a722f1dd889c5a322c9954607f5edcf337bc3684a75a7e',
  [string]$ZigExecutableName = 'zig.exe'
)

$ErrorActionPreference = 'Stop'

$zigVersion = '0.16.0'
$zigArchiveUrl = 'https://ziglang.org/download/0.16.0/zig-x86_64-windows-0.16.0.zip'
$firmwareRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$toolsRoot = if ([string]::IsNullOrWhiteSpace($ToolsRoot)) {
  Join-Path $firmwareRoot '.tools'
} else {
  [System.IO.Path]::GetFullPath($ToolsRoot)
}
$zigRoot = Join-Path $toolsRoot "zig-$zigVersion"
$zigDirectory = Join-Path $zigRoot "zig-x86_64-windows-$zigVersion"
$zigExe = Join-Path $zigDirectory $ZigExecutableName
$zigArchive = Join-Path $zigRoot "zig-x86_64-windows-$zigVersion.zip"

function Test-ZigVersion {
  param([string]$Path)

  if (-not (Test-Path -Path $Path -PathType Leaf)) {
    return $false
  }

  try {
    $actualVersion = (& $Path version | Out-String).Trim()
  } catch {
    return $false
  }

  return (($LASTEXITCODE -eq 0) -and ($actualVersion -ceq $zigVersion))
}

if (-not (Test-ZigVersion $zigExe)) {
  if (Test-Path -Path $zigRoot) {
    Remove-Item -LiteralPath $zigRoot -Recurse -Force
  }
  New-Item -ItemType Directory -Force -Path $zigRoot | Out-Null
  if ([string]::IsNullOrWhiteSpace($ArchivePath)) {
    Invoke-WebRequest -Uri $zigArchiveUrl -OutFile $zigArchive
  } else {
    if (-not (Test-Path -LiteralPath $ArchivePath -PathType Leaf)) {
      throw "Injected Zig archive does not exist: $ArchivePath"
    }
    Copy-Item -LiteralPath $ArchivePath -Destination $zigArchive -Force
  }

  $sha256 = [System.Security.Cryptography.SHA256]::Create()
  $archiveStream = [System.IO.File]::OpenRead($zigArchive)
  try {
    $actualSha256 = [System.BitConverter]::ToString($sha256.ComputeHash($archiveStream)).Replace('-', '').ToLowerInvariant()
  } finally {
    $archiveStream.Dispose()
    $sha256.Dispose()
  }
  if ($actualSha256 -ne $ExpectedSha256) {
    Remove-Item -Force $zigArchive
    throw "Zig archive SHA-256 mismatch: expected $ExpectedSha256, got $actualSha256"
  }

  Expand-Archive -Path $zigArchive -DestinationPath $zigRoot -Force
}

if (-not (Test-ZigVersion $zigExe)) {
  throw "Zig executable version mismatch: expected $zigVersion"
}

Write-Output $zigExe
