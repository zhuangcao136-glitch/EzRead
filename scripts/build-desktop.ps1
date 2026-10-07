# Build a small .NET Framework shell. The shared WebView2 Runtime is not bundled.
param([switch]$Offline, [string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$sdkVersion = '1.0.4191.47'
$sdkHash = 'f492bbf547d0da329553b6727435b677579b1e9f91cc9e4a1ad029366d5f23d0'
$sdkRoot = Join-Path $taskRoot ('.deps\webview2\' + $sdkVersion)
$package = Join-Path $sdkRoot 'sdk.nupkg'
$outputRoot = if ($OutputDirectory) { [IO.Path]::GetFullPath($OutputDirectory) } else { Join-Path $taskRoot 'desktop\bin' }
if ($OutputDirectory -and !$outputRoot.StartsWith(([IO.Path]::GetFullPath($taskRoot) + [IO.Path]::DirectorySeparatorChar), [StringComparison]::OrdinalIgnoreCase)) { throw 'Build output must stay inside the workspace.' }
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (!(Test-Path -LiteralPath $compiler)) { throw 'The Windows .NET Framework C# compiler was not found.' }
New-Item -ItemType Directory -Path $sdkRoot,$outputRoot -Force | Out-Null
if (!(Test-Path -LiteralPath $package)) {
    if ($Offline) { throw 'WebView2 SDK cache is missing. Run this build once with Internet access.' }
    $packageUrl = 'https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/' + $sdkVersion + '/microsoft.web.webview2.' + $sdkVersion + '.nupkg'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $packageUrl -OutFile $package -UseBasicParsing
}
if ((Get-FileHash -LiteralPath $package -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sdkHash) {
    throw 'WebView2 SDK checksum mismatch; build stopped.'
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead($package)
try {
    $fileMap = @{
        'lib/net462/Microsoft.Web.WebView2.Core.dll' = 'Microsoft.Web.WebView2.Core.dll'
        'lib/net462/Microsoft.Web.WebView2.WinForms.dll' = 'Microsoft.Web.WebView2.WinForms.dll'
        'runtimes/win-x64/native/WebView2Loader.dll' = 'WebView2Loader.dll'
        'LICENSE.txt' = 'WebView2-LICENSE.txt'
    }
    foreach ($entryName in $fileMap.Keys) {
        $entry = $archive.GetEntry($entryName)
        if (!$entry) { throw ('Missing SDK file: ' + $entryName) }
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $outputRoot $fileMap[$entryName]), $true)
    }
} finally { $archive.Dispose() }
$arguments = @(
    '/nologo', '/target:winexe', '/platform:x64', '/optimize+', '/utf8output',
    ('/out:' + (Join-Path $outputRoot 'EzRead.Desktop.exe')),
    ('/win32icon:' + (Join-Path $taskRoot 'static\ezread.ico')),
    ('/win32manifest:' + (Join-Path $taskRoot 'desktop\app.manifest')),
    '/reference:System.dll', '/reference:System.Core.dll', '/reference:System.Drawing.dll',
    '/reference:System.Windows.Forms.dll', '/reference:System.Web.Extensions.dll',
    ('/reference:' + (Join-Path $outputRoot 'Microsoft.Web.WebView2.Core.dll')),
    ('/reference:' + (Join-Path $outputRoot 'Microsoft.Web.WebView2.WinForms.dll')),
    (Join-Path $taskRoot 'desktop\EzRead.Desktop.cs')
)
& $compiler @arguments
if ($LASTEXITCODE -ne 0) { throw 'Desktop compilation failed.' }
[IO.File]::WriteAllText((Join-Path $outputRoot 'EzRead.Desktop.exe.config'), '<configuration><startup useLegacyV2RuntimeActivationPolicy="true"><supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.8" /></startup></configuration>')
Get-ChildItem -LiteralPath $outputRoot -File | Select-Object Name,Length
