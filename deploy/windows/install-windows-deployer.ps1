param(
    [Parameter(Mandatory = $true)][string]$EntrypointSource,
    [Parameter(Mandatory = $true)][string]$ConfigSource,
    [string]$InstallRoot = 'C:\ProgramData\qsys'
)

$ErrorActionPreference = 'Stop'
$python = 'C:\qsys\.venv\Scripts\python.exe'
$entrypoint = Join-Path $InstallRoot 'qsys-windows-deploy.py'
$config = Join-Path $InstallRoot 'windows-deployment.json'
$incoming = 'C:\qsys\deployments\incoming'

New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
New-Item -ItemType Directory -Force -Path $incoming | Out-Null

$entrypointTemp = $entrypoint + '.tmp'
Copy-Item -Force $EntrypointSource $entrypointTemp
& $python $entrypointTemp '--help' | Out-Null
Move-Item -Force $entrypointTemp $entrypoint

if (-not (Test-Path $config)) {
    $configTemp = $config + '.tmp'
    Copy-Item -Force $ConfigSource $configTemp
    & $python -c "import json,sys; value=json.load(open(sys.argv[1], encoding='utf-8-sig')); assert value['schema_version']==1" $configTemp
    Move-Item -Force $configTemp $config
}

Write-Output ('installed_entrypoint=' + $entrypoint)
Write-Output ('config=' + $config)
Write-Output ('incoming=' + $incoming)
