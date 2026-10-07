# Vale_playwrite

Automatizador de lançamento de notas fiscais no portal da Vale
(`vale.virtual360.io`), feito com **Playwright**. É a reescrita do projeto
**nfsvale** (Selenium), motivada pela mudança do site pela Vale.

## Status

Fluxos de locação e de serviço funcionando (preenchimento, envio, número do
processo, planilha e aviso à API). Roda em modo teste por padrão.

## Estrutura

```
nota-fiscal-vale
├── main.py                     # baixa as notas, faz o login e chama os dois fluxos
├── browser.py                  # login e ações do portal iguais nos dois fluxos
├── funcs.py                    # planilha, aviso à API e o laço comum
├── api/
│   └── api_client.py           # rotas GET/POST de notas fiscais da Vale (integration-rm)
├── locacao/
│   └── lancar_locacao.py       # nota de locação (FAT, só PDF): todo o processo
├── servico/
│   └── lancar_servico.py       # nota de serviço (NF, PDF + XML): todo o processo
├── entrada/                    # notas baixadas da API (não versionado)
├── .env                        # credenciais (não versionado)
├── requirements.txt
└── README.md
```

## Requisitos

- Python 3.12+
- Chromium do Playwright
- Tesseract OCR (usado pelo `pytesseract`):
  `sudo apt install tesseract-ocr tesseract-ocr-por`

## Instalação

```
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Configuração

Crie um arquivo `.env` na raiz (ele está no `.gitignore`, nunca o versione):

```
VALE_PORTAL_EMAIL=...
VALE_PORTAL_SENHA=...

# API integration-rm - GET /api/notas-fiscais/vale/pendentes
VALE_API_BASE_URL=...
VALE_API_TOKEN=...
```

A planilha Billing a preencher (colunas N° CHAMADO e NF) é, por padrão, a do
**mês de consumo** (mês anterior ao atual) em `M:\BILLING VALE`: o nome do
arquivo precisa ter o mês e o ano (ex.: `Billing Vale Setembro- 2026.xlsx`).
O robô procura o FRS + RF da nota em todas as abas que têm as colunas FRS, RF,
N° CHAMADO e NF. Use `--planilha` para apontar outro arquivo. Rode com a
planilha fechada no Excel.

## Uso

```
python main.py                                  # modo teste, até 50 notas
python main.py --limite 5 --tipo locacao
python main.py --numero 4702 5866               # teste: uma NF e uma FAT
python main.py --numero 5866 --enviar           # envio real
python main.py --headless --enviar              # sem janela (servidor/agendamento)
```

| Opção        | Descrição                                                        |
|--------------|------------------------------------------------------------------|
| `--pasta`    | pasta onde salvar os arquivos baixados (padrão `entrada/`)       |
| `--limite`   | máximo de notas por execução (padrão 50)                         |
| `--numero`   | processa só estas notas (`numero_nf`)                            |
| `--tipo`     | `todos` (padrão), `locacao` ou `servico`                         |
| `--planilha` | planilha Billing a preencher (padrão: mês anterior em `M:\BILLING VALE`) |
| `--enviar`   | envia de verdade e avisa a API                                   |
| `--headless` | roda sem abrir janela e encerra sozinho no final                 |

### Modo teste x envio real

- **Sem `--enviar` (padrão):** preenche o formulário, mas não clica em
  "Ingressar" e não avisa a API. Nada é criado no portal nem gravado.
- **Com `--enviar`:** envia de verdade. Se der erro depois do clique em
  "Ingressar" (resultado incerto), a API **não** é avisada e a nota aparece no
  resumo como "VERIFICAR NO PORTAL" para conferência manual.

### Como as notas são classificadas

- Nota com XML → **serviço** (NF, PDF + XML)
- Nota sem XML cujo PDF termina em `FAT.PDF` → **locação** (FAT, só PDF)
- Qualquer outra é ignorada e listada no resumo.

A locação é processada antes do serviço.

## Execução automática (agendada)

O robô roda sozinho no **2º, 5º e 8º dia útil do mês** (segunda a sexta,
sem feriados nacionais), sem janela e mesmo sem ninguém logado:

- `rodar_agendado.bat` — chamado pelo Agendador de Tarefas todo dia útil às
  08:00; roda `main.py --headless --enviar --dias-uteis 2 5 8`. Nos outros
  dias o robô encerra sem fazer nada.
- A saída de cada execução fica em `logs/execucao_AAAA-MM-DD_HHMM.log`; se um
  envio não confirmar, o print da tela fica em `erros/`.
- Para registrar a tarefa (uma vez só; pede usuário e senha do Windows):
  `powershell -ExecutionPolicy Bypass -File .\agendar_tarefa.ps1`

A planilha é lida pelo caminho de rede `\192.168.0.233\mxtholding\BILLING VALE`
(o mesmo `M:\BILLING VALE`), porque a letra `M:` não existe para a tarefa
quando ninguém está logado.
