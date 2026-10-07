# Reload only a verified idle EzRead HTTP backend; keep the native reader open.
param([Parameter(Mandatory=$true)][int]$ExpectedPid)
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$healthUrl = 'http://127.0.0.1:47831/api/health'
$health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 3
if ($health.pid -ne $ExpectedPid -or [IO.Path]::GetFullPath($health.app_root) -ne $taskRoot) {
    throw 'Backend identity changed; nothing was stopped.'
}
$process = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $ExpectedPid)
$entry = Join-Path $taskRoot 'server.py'
if (!$process -or $process.Name -notin @('python.exe','pythonw.exe') -or !$process.CommandLine.Contains($entry)) {
    throw 'The verified listener is not this EzRead server process; nothing was stopped.'
}
$python = $process.ExecutablePath
$inspection = @'
import collections, contextlib, datetime, json, os, pathlib, sqlite3
directory=pathlib.Path(os.environ['EZREAD_RELOAD_DATA'])
root=pathlib.Path(os.environ['EZREAD_RELOAD_ROOT'])
with contextlib.closing(sqlite3.connect((directory/'library.sqlite3').as_uri()+'?mode=ro',uri=True)) as connection:
    busy=[]
    for (raw,) in connection.execute('SELECT doc FROM papers'):
        doc=json.loads(raw)
        if any(isinstance(v,dict) and v.get('status') in ('running','queued') for v in doc.values()) or any(k.endswith('_status') and v in ('running','queued') for k,v in doc.items()): busy.append(1)
    if busy: raise SystemExit('Active paper jobs exist; backend was not stopped.')
    backup=root/'work/backups'/('before-backend-reload-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True,exist_ok=True)
    with contextlib.closing(sqlite3.connect(backup/'library.sqlite3')) as destination: connection.backup(destination)
    print(json.dumps({'backup':str(backup),'active_jobs':0}))
'@
$env:EZREAD_RELOAD_DATA = $health.data_dir
$env:EZREAD_RELOAD_ROOT = $taskRoot
$env:PYTHONIOENCODING = 'utf-8'
$inspection | & $python -
if ($LASTEXITCODE -ne 0) { throw 'Idle check or database snapshot failed; backend was not stopped.' }
$check = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 3
if ($check.pid -ne $ExpectedPid -or $check.data_dir -ne $health.data_dir -or $check.app_root -ne $health.app_root) { throw 'Backend identity changed; nothing was stopped.' }
Stop-Process -Id $ExpectedPid
$env:EZREAD_DATA_DIR = $health.data_dir
# Launch only the backend. Do not invoke the desktop launcher or depend on a
# non-ASCII script literal decoded as ANSI by Windows PowerShell 5.1.
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stdout = Join-Path $health.data_dir ('server-reload-' + $stamp + '.stdout.log')
$stderr = Join-Path $health.data_dir ('server-reload-' + $stamp + '.stderr.log')
Start-Process -FilePath $python -ArgumentList ('"' + $entry + '"') -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
for ($attempt=0; $attempt -lt 80; $attempt++) {
    Start-Sleep -Milliseconds 150
    try { $newHealth=Invoke-RestMethod -Uri $healthUrl -TimeoutSec 1 } catch { continue }
    if ($newHealth.pid -ne $ExpectedPid -and $newHealth.app_root -eq $health.app_root -and $newHealth.data_dir -eq $health.data_dir) {
        $newHealth | ConvertTo-Json -Compress
        exit 0
    }
}
throw 'Backend reload did not become ready; keep the reader open and inspect data/server.log.'
