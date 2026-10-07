@echo off
rem Chamado pelo Agendador de Tarefas do Windows todo dia util (seg a sex).
rem O proprio robo decide se hoje e o 2o, 5o ou 8o dia util do mes; nos outros
rem dias ele encerra sem fazer nada. Roda sem janela e envia de verdade.
rem A saida de cada execucao fica em logs\execucao_AAAA-MM-DD_HHMM.log

cd /d "%~dp0"
if not exist logs mkdir logs
set PYTHONIOENCODING=utf-8

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmm"') do set DATAHORA=%%i

"venv\Scripts\python.exe" -u main.py --headless --enviar --dias-uteis 2 5 8 >> "logs\execucao_%DATAHORA%.log" 2>&1
