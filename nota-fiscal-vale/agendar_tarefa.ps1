# Registra no Agendador de Tarefas do Windows a execução automática do robô.
#
# Rode UMA vez, no PowerShell, de dentro da pasta do projeto:
#     powershell -ExecutionPolicy Bypass -File .\agendar_tarefa.ps1
#
# Vai pedir, no próprio PowerShell, a senha de login do usuário atual: é a
# conta com que a tarefa roda mesmo com ninguém logado (precisa ter acesso à
# pasta de rede \\192.168.0.233\mxtholding). A senha vai direto para o Windows.
#
# A tarefa chama rodar_agendado.bat de segunda a sexta às 08:00; o próprio
# robô só trabalha no 2º, 5º e 8º dia útil do mês.

$nome = "Robo Notas Fiscais Vale"
$bat = Join-Path $PSScriptRoot "rodar_agendado.bat"

# Senha pedida no próprio PowerShell (não aparece na tela): a janela do
# Get-Credential às vezes abre escondida atrás das outras no servidor.
$usuario = "$env:USERDOMAIN\$env:USERNAME"
$senhaSegura = Read-Host "Senha do Windows de $usuario (não aparece enquanto digita)" -AsSecureString
if ($senhaSegura.Length -eq 0) { Write-Host "Nenhuma senha digitada. Cancelado."; exit 1 }
$cred = New-Object System.Management.Automation.PSCredential($usuario, $senhaSegura)

$acao = New-ScheduledTaskAction -Execute $bat -WorkingDirectory $PSScriptRoot
$gatilho = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At 08:00
$config = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4) `
    -MultipleInstances IgnoreNew

try {
    Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilho -Settings $config `
        -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel Limited -Force `
        -ErrorAction Stop | Out-Null
} catch {
    Write-Host "Não consegui registrar a tarefa: $($_.Exception.Message)"
    Write-Host "Se a mensagem falar de usuário ou senha incorretos, rode de novo e confira a senha."
    exit 1
}

Write-Host "Tarefa '$nome' registrada: seg a sex às 08:00 (o robô roda no 2º, 5º e 8º dia útil)."
Get-ScheduledTask -TaskName $nome | Select-Object TaskName, State
