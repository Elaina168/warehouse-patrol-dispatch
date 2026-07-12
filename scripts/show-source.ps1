param(
  [Parameter(Mandatory = $true)]
  [string]$Path,

  [int]$Skip = 0,
  [int]$First = 80
)

$ErrorActionPreference = "Stop"

$resolvedPath = Resolve-Path -LiteralPath $Path
$content = [System.IO.File]::ReadAllLines($resolvedPath, [System.Text.Encoding]::UTF8)
$end = [Math]::Min($content.Length, $Skip + $First)

for ($index = $Skip; $index -lt $end; $index++) {
  "{0,4}: {1}" -f ($index + 1), $content[$index]
}
