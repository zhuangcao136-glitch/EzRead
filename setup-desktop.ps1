# Create the EzRead shortcut for the current Windows user.
$ErrorActionPreference = 'Stop'
$taskAppRoot = $PSScriptRoot
$taskLauncher = Join-Path $taskAppRoot '启动EzRead.vbs'
$taskIcon = Join-Path $taskAppRoot 'static\ezread.ico'
if (!(Test-Path -LiteralPath $taskLauncher) -or !(Test-Path -LiteralPath $taskIcon)) {
    throw 'EzRead launcher or icon is missing. Keep the complete application folder together.'
}
$taskDesktop = [Environment]::GetFolderPath('Desktop')
$taskShortcutPath = Join-Path $taskDesktop 'EzRead.lnk'
$taskShell = New-Object -ComObject WScript.Shell
$taskShortcut = $taskShell.CreateShortcut($taskShortcutPath)
$taskShortcut.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
$taskShortcut.Arguments = '"' + $taskLauncher + '"'
$taskShortcut.WorkingDirectory = $taskAppRoot
$taskShortcut.IconLocation = $taskIcon + ',0'
$taskShortcut.Description = 'EzRead'
$taskShortcut.WindowStyle = 7
$taskShortcut.Save()
$taskCheck = $taskShell.CreateShortcut($taskShortcutPath)
if ($taskCheck.TargetPath -ne $taskShortcut.TargetPath -or $taskCheck.Arguments -ne $taskShortcut.Arguments) {
    throw 'The saved desktop shortcut failed verification.'
}
[PSCustomObject]@{
    Shortcut = $taskShortcutPath
    Target = $taskCheck.TargetPath
    Arguments = $taskCheck.Arguments
    WorkingDirectory = $taskCheck.WorkingDirectory
    Icon = $taskCheck.IconLocation
}
