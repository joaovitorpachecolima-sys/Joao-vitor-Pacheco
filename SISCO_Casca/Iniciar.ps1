param([switch]$SemNavegador)
# Liga o SISCO (casca) e abre o navegador. Porta e planilha de dados ficam em configuracao.json.
$ErrorActionPreference = 'Stop'
$siscoDirectory = $PSScriptRoot
$siscoPorta = 8780
$siscoConfig = Join-Path $siscoDirectory 'configuracao.json'
if (Test-Path -LiteralPath $siscoConfig) {
    $siscoLido = Get-Content -LiteralPath $siscoConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($siscoLido.porta) { $siscoPorta = [int]$siscoLido.porta }
}
$siscoUrl = "http://127.0.0.1:$siscoPorta"
try {
    $health = Invoke-RestMethod -Uri "$siscoUrl/api/health" -TimeoutSec 2
    if ($health.application -eq 'sisco-casca') {
        if (-not $SemNavegador) { Start-Process $siscoUrl }
        exit 0
    }
    throw "A porta $siscoPorta esta em uso por outro programa. Troque a porta em configuracao.json."
} catch {
    if ($_.Exception.Message -like '*outro programa*') { throw }
}
# Python 3.10 ou mais novo: o lancador "py", o "python" do PATH ou o runtime do Codex, nessa ordem.
# Cada candidato e testado e o caminho real do python.exe e usado (assim o Parar.ps1 encontra o processo).
$siscoPython = $null
$siscoCandidatos = @(
    @{ Comando = 'py'; Extra = @('-3') },
    @{ Comando = 'python'; Extra = @() },
    @{ Comando = (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'); Extra = @() }
)
foreach ($siscoCandidato in $siscoCandidatos) {
    if (-not (Get-Command $siscoCandidato.Comando -ErrorAction SilentlyContinue)) { continue }
    try {
        $siscoExtra = $siscoCandidato.Extra
        $siscoTeste = & $siscoCandidato.Comando @siscoExtra -c 'import sys; print(sys.executable if sys.version_info >= (3, 10) else 0)' 2>$null
        $siscoTeste = ([string]$siscoTeste).Trim()
        if ($LASTEXITCODE -eq 0 -and $siscoTeste -and (Test-Path -LiteralPath $siscoTeste)) { $siscoPython = $siscoTeste; break }
    } catch { }
}
if (-not $siscoPython) { throw 'Python 3.10+ nao encontrado. Instale o Python (python.org) e rode de novo.' }
$siscoArgs = @()
$siscoData = Join-Path $siscoDirectory 'dados'
New-Item -ItemType Directory -Path $siscoData -Force | Out-Null
$siscoApp = Join-Path $siscoDirectory 'app.py'
$process = Start-Process -FilePath $siscoPython -ArgumentList ($siscoArgs + @('-B', ('"' + $siscoApp + '"'))) -WorkingDirectory $siscoDirectory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $siscoData 'servico.log') -RedirectStandardError (Join-Path $siscoData 'erro.log')
$process.Id | Set-Content -LiteralPath (Join-Path $siscoData 'servico.pid')
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 500
    if ($process.HasExited) { throw 'O programa encerrou. Consulte dados\erro.log.' }
    try {
        $health = Invoke-RestMethod -Uri "$siscoUrl/api/health" -TimeoutSec 1
        if ($health.application -eq 'sisco-casca') { if (-not $SemNavegador) { Start-Process $siscoUrl }; exit 0 }
    } catch { }
}
throw 'O programa ainda nao respondeu. Consulte dados\erro.log.'
