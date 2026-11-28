$ErrorActionPreference = 'Stop'
$siscoPidPath = Join-Path $PSScriptRoot 'dados\servico.pid'
if (-not (Test-Path -LiteralPath $siscoPidPath)) { Write-Host 'Nenhum servico iniciado pelo atalho foi registrado.'; exit 0 }
$siscoProcessId = [int](Get-Content -LiteralPath $siscoPidPath -Raw)
$siscoProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $siscoProcessId"
$siscoExpected = Join-Path $PSScriptRoot 'app.py'
if ($null -eq $siscoProcess) { Write-Host 'O programa ja esta parado.'; exit 0 }
if (-not $siscoProcess.CommandLine.Contains($siscoExpected)) { throw 'O processo registrado pertence a outro programa. Nenhum processo foi encerrado.' }
Stop-Process -Id $siscoProcessId
Write-Host 'SISCO parado. A planilha de dados e a base local continuam guardadas.'
