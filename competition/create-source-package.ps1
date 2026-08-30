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
$sourcePackagePaths = @(
  ".editorconfig",
  ".gitignore",
  "README.md",
  "package.json",
  "frontend/package.json",
  "frontend/package-lock.json",
  "frontend/index.html",
  "frontend/vite.config.ts",
  "frontend/tsconfig.json",
  "frontend/tsconfig.app.json",
  "frontend/tsconfig.node.json",
  "frontend/src",
  "backend/requirements.txt",
  "backend/requirements.lock.txt",
  "backend/app",
  "competition/requirements-build.lock.txt",
  "competition/BUILDING.md",
  "competition/build-windows-package.ps1",
  "competition/create-source-package.ps1",
  "competition/launcher.py",
  "competition/packaging.py",
  "competition/warehouse_patrol.spec",
  "competition/3s/manifests",
  "scripts",
  "docs/algorithm.md",
  "docs/baseline.md",
  "docs/demo.md",
  "docs/environment.md",
  "docs/testing-guide.md"
)
$requiredSourceFiles = @(
  "README.md",
  "package.json",
  "frontend/package.json",
  "frontend/package-lock.json",
  "frontend/index.html",
  "frontend/vite.config.ts",
  "frontend/src/main.tsx",
  "backend/requirements.txt",
  "backend/requirements.lock.txt",
  "backend/app/main.py",
  "competition/requirements-build.lock.txt",
  "competition/BUILDING.md",
  "competition/build-windows-package.ps1",
  "competition/create-source-package.ps1",
  "competition/launcher.py",
  "competition/packaging.py",
  "competition/warehouse_patrol.spec",
  "competition/3s/manifests/main-demo.json",
  "competition/3s/manifests/safety-demo.json",
  "scripts/start-dev.ps1",
  "scripts/stop-dev.ps1",
  "docs/algorithm.md",
  "docs/baseline.md",
  "docs/demo.md",
  "docs/environment.md",
  "docs/testing-guide.md"
)
$restrictedText = @(
  ([char[]](71, 80, 84) -join ""),
  ([char[]](67, 104, 97, 116, 71, 80, 84) -join ""),
  ([char[]](79, 112, 101, 110, 65, 73) -join ""),
  ([char[]](20154, 24037, 26234, 33021) -join ""),
  ([char[]](26426, 22120, 23398, 20064) -join ""),
  ([char[]](23398, 20064, 22411) -join ""),
  ([char[]](22823, 27169, 22411) -join ""),
  ([char[]](29983, 25104, 24335) -join "")
)

try {
  $treeEntries = @(& git -C $resolvedRepositoryRoot ls-tree -r --name-only HEAD)
  if ($LASTEXITCODE -ne 0) {
    throw "无法读取 Git 提交树。"
  }
  foreach ($requiredPath in $requiredSourceFiles) {
    if (-not $treeEntries.Contains($requiredPath)) {
      throw "源码包缺少必需文件：$requiredPath"
    }
  }

  $archiveArguments = @(
    "archive",
    "--format=zip",
    "--output=$temporaryPath",
    "HEAD",
    "--"
  ) + $sourcePackagePaths
  & git -C $resolvedRepositoryRoot @archiveArguments
  if ($LASTEXITCODE -ne 0) {
    throw "生成源码包失败。"
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
      $allowed = $false
      foreach ($sourcePath in $sourcePackagePaths) {
        if (
          $entry.FullName.Equals($sourcePath, [System.StringComparison]::Ordinal) -or
          $entry.FullName.StartsWith(
            "$sourcePath/",
            [System.StringComparison]::Ordinal
          )
        ) {
          $allowed = $true
          break
        }
      }
      if (-not $allowed) {
        throw "源码包包含未批准路径：$($entry.FullName)"
      }
      foreach ($term in $restrictedText) {
        if ($entry.FullName.IndexOf($term, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
          throw "源码包文件名包含受限文字：$($entry.FullName)"
        }
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
      foreach ($term in $restrictedText) {
        if ($contents.IndexOf($term, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
          throw "源码包包含受限文字：$($entry.FullName)"
        }
      }
    }
    foreach ($requiredPath in $requiredSourceFiles) {
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
