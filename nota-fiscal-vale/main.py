"""
Ponto de entrada do automatizador (Playwright) — notas da Vale.

Baixa as notas pendentes da API, faz o login uma vez e chama os dois fluxos,
locação primeiro e serviço depois:

    locacao/lancar_locacao.py  (FAT, só PDF)
    servico/lancar_servico.py  (NF, PDF + XML)

Cada um tem todo o processo da nota: preencher no portal, enviar, ler o nº do
processo, preencher a planilha e avisar a API.

SEM --enviar (padrão, modo teste): preenche o formulário mas NÃO clica em
"Ingressar" e NÃO avisa a API. Nada é criado no portal nem gravado.
COM --enviar: envia de verdade. Se der erro DEPOIS do clique em "Ingressar"
(resultado incerto), a API NÃO é avisada — fica no resumo como "VERIFICAR NO
PORTAL" para você conferir à mão.

Uso:
    python main.py                       # teste, 2 primeiras notas
    python main.py --limite 5 --tipo locacao
    python main.py --numero 5866 --enviar
    python main.py --numero 4702 5866    # teste: uma NF e uma FAT
"""

import argparse
import os
import sys

from playwright.sync_api import sync_playwright

from api.api_client import NotasValeApiError, baixar_notas_pendentes
from browser import login
from locacao.lancar_locacao import lancar_locacao
from servico.lancar_servico import lancar_servico

PLANILHA_PADRAO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Billing Agosto 2026 (Cópia).xlsx")
PASTA_PADRAO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "entrada")


def classificar(resultados, numero, tipo):
    """Devolve ([(r, 'locacao'|'servico')], [(numero_nf, motivo)])."""
    a_processar, ignoradas = [], []
    for r in resultados:
        if r["erro"]:
            ignoradas.append((r["numero_nf"], f"erro da API: {r['erro']}"))
            continue
        if numero and r["numero_nf"] not in numero:
            continue
        if r["caminho_xml"]:
            kind = "servico"
        elif r["caminho_pdf"].upper().endswith("FAT.PDF"):
            kind = "locacao"
        else:
            ignoradas.append((r["numero_nf"], "tipo de nota não reconhecido"))
            continue
        if tipo in ("todos", kind):
            a_processar.append((r, kind))
    return a_processar, ignoradas


def main():
    parser = argparse.ArgumentParser(description="Lança notas de locação e serviço na Vale.")
    parser.add_argument("--pasta", default=PASTA_PADRAO, help="pasta onde salvar os arquivos")
    parser.add_argument("--limite", type=int, default=2, help="máximo de notas (padrão 2)")
    parser.add_argument("--numero", nargs="+", help="processar só estas notas (numero_nf)")
    parser.add_argument("--tipo", choices=["todos", "locacao", "servico"], default="todos")
    parser.add_argument("--planilha", default=PLANILHA_PADRAO, help="planilha Billing a preencher (N° CHAMADO e NF)")
    parser.add_argument("--enviar", action="store_true", help="envia de verdade e avisa a API")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="roda sem abrir janela e encerra sozinho no final (para servidor/agendamento)",
    )
    args = parser.parse_args()

    try:
        resultados = baixar_notas_pendentes(args.pasta)
    except NotasValeApiError as e:
        sys.exit(f"Erro na API: {e}")

    fila, ignoradas = classificar(resultados, args.numero, args.tipo)
    # Locação primeiro (fluxo validado), depois serviço. sort() é estável:
    # dentro de cada tipo continua a ordem em que a API devolveu.
    fila.sort(key=lambda item: item[1] != "locacao")
    print(f"\n{len(resultados)} nota(s) recebida(s); {len(fila)} no filtro, {len(ignoradas)} ignorada(s).")
    fila = fila[: args.limite]
    locacoes = [r for r, kind in fila if kind == "locacao"]
    servicos = [r for r, kind in fila if kind == "servico"]
    modo = "ENVIO REAL" if args.enviar else "MODO TESTE (nada será enviado)"
    print(f"Processando {len(fila)} (limite {args.limite}): {len(locacoes)} locação, {len(servicos)} serviço — {modo}\n")

    resumo = []
    with sync_playwright() as playwright:
        navegador = playwright.chromium.launch(headless=args.headless)
        context = navegador.new_context()

        if fila:
            try:
                login(context.new_page())
            except Exception as e:  # noqa: BLE001
                sys.exit(f"Falha no login do portal: {str(e).splitlines()[0]}")

        resumo += lancar_locacao(context, locacoes, args.enviar, args.planilha)
        resumo += lancar_servico(context, servicos, args.enviar, args.planilha)

        print("\n=== Resumo ===")
        for numero, kind, texto in resumo:
            print(f"NF {numero} ({kind}): {texto}")
        for numero, motivo in ignoradas:
            print(f"NF {numero}: ignorada — {motivo}")

        navegador.close()


if __name__ == "__main__":
    main()
