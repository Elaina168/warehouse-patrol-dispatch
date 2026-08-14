param(
  [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot),
  [string]$OutputPath = (Join-Path (Split-Path -Parent $PSScriptRoot) "output\3s-competition-build\WarehousePatrol-source.zip")
)

if ($PSVersionTable.PSVersion.Major -lt 7) {
  throw "PowerShell 7 or later is required."
}

$ErrorActionPreference = "Stop"
$resolvedRepositoryRoot = (Resolve-Path -LiteralPath $RepositoryRoot).Path
$resolvedOutputPath = [System.IO.Path]::GetFullPath($OutputPath)

$status = & git -C $resolvedRepositoryRoot status --porcelain
if ($LASTEXITCODE -ne 0) {
  throw "无法读取 Git 工作树状态。"
}
if (@($status).Count -ne 0) {
  throw "Git 工作树不干净，拒绝生成源码包。"
}

$outputDirectory = Split-Path -Parent $resolvedOutputPath
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
$temporaryPath = Join-Path $outputDirectory ("." + [System.IO.Path]::GetRandomFileName() + ".zip")

try {
  & git -C $resolvedRepositoryRoot archive --format=zip "--output=$temporaryPath" HEAD
  if ($LASTEXITCODE -ne 0) {
    throw "git archive HEAD 生成源码包失败。"
  }

  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $archive = [System.IO.Compression.ZipFile]::OpenRead($temporaryPath)
  try {
    $entryNames = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
    foreach ($entry in $archive.Entries) {
      $null = $entryNames.Add($entry.FullName)
      if ($entry.FullName.EndsWith("/", [System.StringComparison]::Ordinal)) {
        continue
      }
      $reader = [System.IO.StreamReader]::new($entry.Open(), [System.Text.UTF8Encoding]::new($false, $false), $true)
      try {
        $contents = $reader.ReadToEnd()
      } finally {
        $reader.Dispose()
      }
      if ($contents -match '(?i)[A-Z]:[\\/]+Users[\\/]+[^\\/\r\n]+') {
        throw "源码包包含 Windows 用户目录绝对路径：$($entry.FullName)"
      }
    }
    foreach ($requiredPath in @(
        "package.json",
        "frontend/package.json",
        "frontend/package-lock.json",
        "backend/requirements.lock.txt",
        "competition/requirements-build.lock.txt",
        "competition/BUILDING.md",
        "competition/build-windows-package.ps1",
        "competition/launcher.py",
        "competition/packaging.py",
        "competition/warehouse_patrol.spec",
        "competition/3s/manifests/main-demo.json",
        "backend/competition/evidence.py"
      )) {
      if (-not $entryNames.Contains($requiredPath)) {
        throw "源码包缺少必需文件：$requiredPath"
      }
    }
  } finally {
    $archive.Dispose()
  }

  Move-Item -LiteralPath $temporaryPath -Destination $resolvedOutputPath -Force
  Write-Output $resolvedOutputPath
} finally {
  if (Test-Path -LiteralPath $temporaryPath) {
    Remove-Item -LiteralPath $temporaryPath -Force
  }
}
