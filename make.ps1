<#
.SYNOPSIS
    该脚本用于打包 "Seraphine" 。

.PARAMETER dest
    输出的目标路径。默认为当前目录。

.PARAMETER dbg
    是否启用调试模式。如果启用，将不会删除 `.\dist` 目录，也不会创建 7z 文件。

.EXAMPLE
    .\make.ps1 -dbg
#>

param(
    [Parameter()]
    [String]$dest = ".",
    [Switch]$dbg
)

if ($dbg -and (Test-Path .\dist)) {
    rm -r -Force .\dist
}

pyinstaller -w -i .\app\resource\images\logo.ico main.py
rm -r -fo .\build
rm -r -fo .\main.spec
rni -path .\dist\main -newName Seraphine
rni -path .\dist\Seraphine\main.exe -newName Seraphine.exe
cpi .\app -destination .\dist\Seraphine -recurse
rm -r .\dist\Seraphine\app\common
rm -r .\dist\Seraphine\app\components
rm -r .\dist\Seraphine\app\lol
rm -Path .\dist\Seraphine\app\resource\game* -r
rm -r .\dist\Seraphine\app\resource\i18n\Seraphine.zh_CN.ts
# 客户端窗口修复已改成本进程内 ctypes 实现 (app/common/win_window.py),
# bin 目录只剩一份 C 源码和说明, 运行时一个都不需要
rm -r .\dist\Seraphine\app\resource\bin
rm -r .\dist\Seraphine\app\view

$files = Get-ChildItem -Path ".\dist\Seraphine\*" -Recurse |
    Select-Object -ExpandProperty FullName |
    ForEach-Object { $_.Replace((Resolve-Path ".\dist\Seraphine").Path + "\", "") }

$files | Out-File -FilePath ".\dist\Seraphine\filelist.txt" -Encoding UTF8

if (! $dbg) {
    7z a $dest\Seraphine.7z .\dist\Seraphine\* -r
    rm -r .\dist
}
