param(
    [Parameter(Position = 0)]
    [string]$Message = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

$BundledPython = "C:\Users\kevin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if (Test-Path $BundledPython) {
    $Python = $BundledPython
} else {
    $Python = "python"
}

& $Python -m unittest discover -s tests -v

$Status = git status --porcelain
if (-not $Status) {
    Write-Host "No local changes to sync."
    exit 0
}

if (-not $Message) {
    $Stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $Message = "Update project $Stamp"
}

git add .
git commit -m $Message
git push
