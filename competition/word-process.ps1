function Get-IsolatedWordProcessId {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][object]$WordApplication
  )

  if (-not ("WordProcess.NativeMethods" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
namespace WordProcess {
  public static class NativeMethods {
    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
  }
}
"@
  }

  $probeDocument = $null
  $probeWindow = $null
  try {
    $windowHandle = $WordApplication.Hwnd
    if ($null -eq $windowHandle -or [IntPtr]$windowHandle -eq [IntPtr]::Zero) {
      $probeDocument = $WordApplication.Documents.Add()
      $probeWindow = $probeDocument.ActiveWindow
      if ($null -eq $probeWindow) {
        throw "无法创建隔离 Word 探测窗口。"
      }
      $windowHandle = $probeWindow.Hwnd
    }
    if ($null -eq $windowHandle -or [IntPtr]$windowHandle -eq [IntPtr]::Zero) {
      throw "无法取得隔离 Word 窗口句柄。"
    }

    [uint32]$wordPid = 0
    $null = [WordProcess.NativeMethods]::GetWindowThreadProcessId(
      [IntPtr]$windowHandle,
      [ref]$wordPid
    )
    if ($wordPid -le 0) {
      throw "无法识别隔离 Word 进程。"
    }
    return [int]$wordPid
  } finally {
    if ($null -ne $probeWindow -and [Runtime.InteropServices.Marshal]::IsComObject($probeWindow)) {
      [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($probeWindow)
    }
    if ($null -ne $probeDocument) {
      try { $probeDocument.Close($false) } catch {}
      if ([Runtime.InteropServices.Marshal]::IsComObject($probeDocument)) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($probeDocument)
      }
    }
  }
}
