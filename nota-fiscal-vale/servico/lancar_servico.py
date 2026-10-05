"""
Nota de SERVIÇO (NF, PDF + XML) — todo o processo:

    subir XML e PDF no formulário "Nota Fiscal de Serviço" do portal da Vale
    (que lê o resto do XML sozinho) -> gestor, vencimento e INSS -> (com
    enviar) clicar em "Ingressar Nota" -> ler o nº do processo -> planilha + API.

Ponto de entrada: lancar_servico(context, notas, enviar, planilha). O login é
feito antes, pelo main.py (browser.login).
"""

import os
import re
from datetime import datetime, timedelta

from playwright.sync_api import Page

import funcs
from api.api_client import STATUS_PULADO
from browser import ESPERA_MS, PORTAL_URL, concluir_envio, ingressar_com_confirmacao
from funcs import DIAS_VENCIMENTO, EnvioIncerto
from locacao.lancar_locacao import preencher_e_validar

GESTOR_CONTRATO = "Igor.alcantara@vale.com"

# "data_emissao" e "data_vencimento" não vêm de fora: o próprio portal
# preenche a Data de Emissão sozinho ao processar o XML (campo
# #tax_document_issue_date) — a gente lê esse valor direto da tela em vez de
# reparsear o XML (ver preencher_data_vencimento).

# ---------------------------------------------------------------------------
# Preenchimento do formulário no portal
# ---------------------------------------------------------------------------

def abrir_formulario_nota_servico(page: Page):
    # Igual ao "Outro Documento" (nota_locacao): em vez de passar pelo
    # menu frágil ("Adicionar" -> selecionar Tomador -> popup), vamos
    # direto pra URL do formulário — confirmada pelo DevTools.
    page.goto(f"{PORTAL_URL}nf/tax_documents/service_invoice/new")
    print("Abriu direto o formulário de 'Nota Fiscal de Serviço' pela URL")
    page.wait_for_timeout(ESPERA_MS)

    return page  # sem popup — o formulário abre na mesma aba


def subir_arquivos_nota(form, dados):
    form.get_by_role("button", name="XML da Nota Fiscal").set_input_files(
        dados["caminho_xml"]
    )
    print(f"Subiu o XML da nota: {dados['caminho_xml']}")
    form.wait_for_timeout(ESPERA_MS)

    form.get_by_role("button", name="* PDF da Nota Fiscal").set_input_files(
        dados["caminho_pdf"]
    )
    print(f"Subiu o PDF da nota: {dados['caminho_pdf']}")
    form.wait_for_timeout(ESPERA_MS)


def preencher_dados_nota(form, dados):
    # Campo tem "setter" customizado (data-has-setter="true"), igual o
    # Número da Nota no nota_locacao — .fill() não é confiável, precisa
    # digitar tecla por tecla e confirmar com Tab. Esse campo também tem
    # data-reload-form: preencher ele dispara a recarga assíncrona que
    # carrega as opções do combobox "Alíquota do INSS" (ver preencher_inss).
    #
    # Já tentamos esperar aqui por uma requisição de rede específica
    # (possible_values + field=249), mas esse padrão de URL nunca bateu de
    # verdade — só travava até estourar o timeout. Quem garante que as
    # opções carregaram é a própria preencher_inss, esperando o elemento
    # da opção aparecer na tela quando chega a hora de usá-lo.
    campo_gestor = form.locator("#tax_document_requester_area")
    campo_gestor.click()
    campo_gestor.press_sequentially(dados["gestor_contrato"])
    campo_gestor.press("Tab")
    print(f"Preencheu o Gestor do Contrato: {dados['gestor_contrato']}")

    print("Esperando a rede ficar parada (recarga do formulário terminar)...")
    form.wait_for_load_state("networkidle", timeout=60_000)
    print("Recarga do formulário concluída")
    form.wait_for_timeout(ESPERA_MS)

    preencher_data_vencimento(form)


def preencher_data_vencimento(form):
    # Testando no navegador, vimos que a Data de Emissão (#tax_document_
    # issue_date) é preenchida sozinha pelo portal ao processar o XML
    # anexado — não precisa reparsear o XML pra saber essa data, só ler
    # o valor depois que o portal preenche.
    campo_emissao = form.locator("#tax_document_issue_date")

    if not campo_emissao.input_value():
        print(
            "Campo 'Data de Emissão' ainda vazio, esperando o portal preencher "
            "(ou preenchimento manual)..."
        )
        form.wait_for_function(
            "() => document.querySelector('#tax_document_issue_date').value !== ''",
            timeout=300_000,
        )

    data_emissao = datetime.strptime(campo_emissao.input_value(), "%Y-%m-%d")
    data_vencimento = data_emissao + timedelta(days=DIAS_VENCIMENTO)

    form.get_by_role("textbox", name="* Data de Vencimento").fill(
        data_vencimento.strftime("%Y-%m-%d")
    )
    print(
        f"Emissão: {data_emissao:%d/%m/%Y} -> "
        f"Vencimento: {data_vencimento:%d/%m/%Y}"
    )
    form.wait_for_timeout(ESPERA_MS)


def preencher_inss(form):
    # ALÍQUOTA DO INSS é Select2 "with-ajax" (busca as opções via POST em
    # /processes/possible_values?field=249) — confirmado testando: o
    # widget select2 (o .select2-container) só é criado quando a página
    # rola até o <select> ORIGINAL (#tax_document_inss_tax_rate), não até
    # o container em si — ele nem existe antes disso. Por isso rolamos
    # até o <select>, não até o container, senão fica esperando por um
    # elemento que só aparece depois desse scroll.
    select_inss = form.locator("#tax_document_inss_tax_rate")
    select_inss.scroll_into_view_if_needed()
    form.wait_for_timeout(1000)

    campo = form.locator("#tax_document_inss_tax_rate + .select2-container")
    campo.wait_for(timeout=10_000)
    campo.click()
    print("Abriu o dropdown 'Alíquota do INSS'")

    opcoes = form.locator(".select2-container--open .select2-results__option")
    opcoes.first.wait_for(timeout=30_000)

    busca = form.locator(".select2-container--open .select2-search__field")
    if busca.count() and busca.is_visible():
        busca.fill("0")
        form.wait_for_timeout(ESPERA_MS)
    else:
        print("Dropdown sem campo de busca (poucas opções) — selecionando direto")

    print("Opções disponíveis:", opcoes.all_text_contents())

    form.locator(
        ".select2-container--open .select2-results__option",
        has_text=re.compile(r"^\s*0(,00)?\s*%\s*$"),
    ).first.click()
    print("Selecionou a opção de 0% em 'Alíquota do INSS'")
    form.wait_for_timeout(ESPERA_MS)

    valor_selecionado = form.locator("#tax_document_inss_tax_rate").input_value()
    assert valor_selecionado == "0.00", (
        f"Esperava '0.00' na Alíquota do INSS, mas o campo ficou com {valor_selecionado!r}"
    )
    print("Confirmado: Alíquota do INSS = 0.00")

    campo_valor = form.locator("#tax_document_inss_value")
    campo_valor.fill("0")
    campo_valor.press("Tab")  # dispara a máscara/validação do campo
    print("Preencheu o Valor do INSS: 0")
    form.wait_for_timeout(ESPERA_MS)


def preencher_rf(form, dados):
    # O portal lê o FRS do XML sozinho, mas o "Relatório de Faturamento (RF)"
    # fica vazio e é obrigatório: sem ele o navegador bloqueia o envio e o
    # clique em "Ingressar Nota" não faz nada.
    preencher_e_validar(
        form,
        form.locator("#tax_document_invoice_items_attributes_0_billing_report_code"),
        dados["rf"],
        "Relatório de Faturamento (RF)",
    )


def ingressar_nota(form):
    # Os passos extras da gravação (busca com "Insert"/"Enter" e um 2º clique)
    # eram só o preenchimento da Alíquota/Valor do INSS, que já é feito em
    # preencher_inss() antes deste clique — um clique basta.
    ingressar_com_confirmacao(form, "Ingressar Nota")


# ---------------------------------------------------------------------------
# Fluxo completo da nota
# ---------------------------------------------------------------------------

def lancar_uma(context, r, enviar):
    """Retorna (status_para_api | None, texto_do_resumo)."""
    dados = {
        "caminho_xml": os.path.abspath(r["caminho_xml"]),
        "caminho_pdf": os.path.abspath(r["caminho_pdf"]),
        "gestor_contrato": GESTOR_CONTRATO,
    }

    r["frs"], r["rf"] = funcs.extrair_frs_rf_servico(r["caminho_pdf"])
    if not r["rf"]:
        return STATUS_PULADO, "pulada: RF não encontrado no PDF"
    dados["rf"] = r["rf"]

    form = context.new_page()
    r["_aba"] = form
    abrir_formulario_nota_servico(form)
    subir_arquivos_nota(form, dados)
    preencher_dados_nota(form, dados)
    preencher_inss(form)
    preencher_rf(form, dados)

    if not enviar:
        return None, "preenchida (não enviada)"

    try:
        ingressar_nota(form)
    except Exception as e:  # noqa: BLE001
        raise EnvioIncerto(str(e).splitlines()[0]) from e
    return concluir_envio(form, r)


def lancar_servico(context, notas, enviar, planilha):
    """Lança todas as notas NF da lista. Retorna [(numero_nf, 'servico', texto)]."""
    return funcs.processar_notas(context, notas, "servico", lancar_uma, enviar, planilha)
