$ErrorActionPreference = 'Stop'
# Envia UMA mensagem de teste pelo Outlook clássico, só para o e-mail de teste de configuracao.json.
$siscoSubmissionStarted = $false
try {
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
$siscoMessage = [Console]::In.ReadToEnd() | ConvertFrom-Json
$siscoTeste = 'voce@exemplo.com'
$siscoConfig = Join-Path $PSScriptRoot 'configuracao.json'
if (Test-Path -LiteralPath $siscoConfig) {
    $siscoLido = Get-Content -LiteralPath $siscoConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($siscoLido.email_de_teste) { $siscoTeste = [string]$siscoLido.email_de_teste }
}
if ($siscoMessage.to -cne $siscoTeste) { throw 'Destinatario fora do ambiente de teste.' }
if (-not $siscoMessage.subject.StartsWith('[TESTE SISCO] ')) { throw 'Assunto invalido.' }
$siscoOutlook = New-Object -ComObject Outlook.Application
$siscoMail = $siscoOutlook.CreateItem(0)
foreach ($siscoCandidate in $siscoOutlook.Session.Accounts) {
    # Se a conta do e-mail de teste estiver no Outlook, envia por ela; senao, pela conta padrao.
    if ($siscoCandidate.SmtpAddress -ieq $siscoTeste) { $siscoMail.SendUsingAccount = $siscoCandidate; break }
}
$siscoMail.To = $siscoTeste
$siscoMail.CC = ''
$siscoMail.BCC = ''
$siscoMail.Subject = $siscoMessage.subject
$siscoMail.Body = $siscoMessage.body
if ($siscoMessage.attachment) {
    $siscoAllowed = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'dados\documentos_teste')) + [IO.Path]::DirectorySeparatorChar
    $siscoAttachment = [IO.Path]::GetFullPath($siscoMessage.attachment)
    if (-not $siscoAttachment.StartsWith($siscoAllowed, [StringComparison]::OrdinalIgnoreCase) -or [IO.Path]::GetExtension($siscoAttachment) -ne '.docx') { throw 'Anexo fora da pasta de testes.' }
    [void]$siscoMail.Attachments.Add($siscoAttachment)
}
if (-not $siscoMail.Recipients.ResolveAll()) { throw 'Destinatario nao resolvido pelo Outlook.' }
$siscoSubmissionStarted = $true
$siscoMail.Send()
[Console]::WriteLine('SISCO_SUBMETIDO')
} catch {
    if (-not $siscoSubmissionStarted) { [Console]::WriteLine('SISCO_NOT_SUBMITTED') }
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
