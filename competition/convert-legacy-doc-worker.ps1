param(
  [Parameter(Mandatory = $true)][string]$SourcePath,
  [Parameter(Mandatory = $true)][string]$DestinationPath,
  [Parameter(Mandatory = $true)][string]$PidFile
)

$ErrorActionPreference = "Stop"
$word = $null
$document = $null
. (Join-Path $PSScriptRoot "word-process.ps1")

try {
  if (Test-Path -LiteralPath $DestinationPath) {
    throw "转换目标已存在。"
  }

  $word = New-Object -ComObject Word.Application
  $word.Visible = $false
  $word.DisplayAlerts = 0

  $wordPid = Get-IsolatedWordProcessId -WordApplication $word
  Set-Content -LiteralPath $PidFile -Value $wordPid -Encoding ascii -NoNewline

  $document = $word.Documents.Open(
    $SourcePath,
    $false,
    $true,
    $false,
    "",
    "",
    $false,
    "",
    "",
    0,
    65001,
    $false,
    $false,
    0,
    $true,
    $false
  )
  $document.SaveAs2($DestinationPath, 16)
} finally {
  if ($null -ne $document) {
    try { $document.Close($false) } catch {}
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document)
  }
  if ($null -ne $word) {
    try { $word.Quit() } catch {}
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($word)
  }
  [GC]::Collect()
  [GC]::WaitForPendingFinalizers()
}
