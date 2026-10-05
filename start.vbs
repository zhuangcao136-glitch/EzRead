Option Explicit
Dim shell, fs, root, runtime, command
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(WScript.ScriptFullName)
runtime = shell.ExpandEnvironmentStrings("%USERPROFILE%") & "\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"
If Not fs.FileExists(runtime) Then
  runtime = shell.ExpandEnvironmentStrings("%USERPROFILE%") & "\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
End If
If Not fs.FileExists(runtime) Then
  MsgBox "Python runtime not found. See README.md.", 16, "EzRead"
  WScript.Quit 1
End If
command = Chr(34) & runtime & Chr(34) & " " & Chr(34) & root & "\launch.pyw" & Chr(34)
shell.Run command, 0, False
