"""Windows file actions for EzRead's local UI. No shell interpolation of paths."""
from __future__ import annotations

import base64
import os
from pathlib import Path
import subprocess


# WinForms writes persistent FileDrop data; STA is required by Windows Clipboard.
COPY_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
$files = New-Object System.Collections.Specialized.StringCollection
[void]$files.Add($env:EZREAD_CLIPBOARD_PDF)
[System.Windows.Forms.Clipboard]::SetFileDropList($files)
"""


def copy_pdf(path: Path):
    if os.name != 'nt':
        raise ValueError('复制 PDF 文件到剪贴板需要在 Windows 上使用。')
    environment = os.environ.copy()
    environment['EZREAD_CLIPBOARD_PDF'] = str(path.resolve(strict=True))
    powershell = Path(environment.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    try:
        subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Sta', '-EncodedCommand',
                        base64.b64encode(COPY_SCRIPT.encode('utf-16-le')).decode('ascii')],
                       env=environment, check=True, capture_output=True, timeout=15,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise ValueError('复制 PDF 超时，请稍后重试。') from None
    except (OSError, subprocess.CalledProcessError):
        raise ValueError('未能复制 PDF 文件，剪贴板可能正被其他程序使用，请重试。') from None


def reveal_pdf(path: Path):
    if os.name != 'nt':
        raise ValueError('在文件夹显示需要在 Windows 上使用。')
    explorer = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'explorer.exe'
    try:
        subprocess.Popen([str(explorer), '/select,', str(path.resolve(strict=True))],
                         creationflags=getattr(subprocess, 'CREATE_BREAKAWAY_FROM_JOB', 0))
    except OSError:
        raise ValueError('未能打开文件夹，请稍后重试。') from None
