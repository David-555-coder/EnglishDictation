$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$taskPython = 'C:\Program Files\Python311\pythonw.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw '找不到本机用于验证的 Python 3.11；请联系维护者，不会修改正式软件。' }
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = "$taskRoot;$taskRoot\.test-deps"
Start-Process -FilePath $taskPython -ArgumentList @('-m','tools.translation_prototype.ui') -WorkingDirectory $taskRoot -WindowStyle Hidden
