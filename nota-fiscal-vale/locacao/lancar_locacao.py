"""
Nota de LOCAÇÃO (FAT, só PDF) — todo o processo, do PDF ao registro do processo:

    extrair os dados do PDF -> preencher o formulário "Outro Documento"
    (Aluguel) no portal da Vale -> (com enviar) clicar em "Ingressar Documento"
    -> ler o nº do processo -> planilha + API.

Ponto de entrada: lancar_locacao(context, notas, enviar, planilha). O login é
feito antes, pelo main.py (browser.login).
"""

import os
import re
from datetime import datetime, timedelta

import pdfplumber
from playwright.sync_api import Page

import funcs
from api.api_client import STATUS_PULADO
from browser import ESPERA_MS, PORTAL_URL, concluir_envio, ingressar_com_confirmacao
from funcs import DadosInvalidos, EnvioIncerto

CNPJ_EMISSOR = "04188944000195"
CIDADE_EMISSOR = "betim"
CNPJ_DESTINATARIO = "33592510000154"

CAMPOS_OBRIGATORIOS = (
    "numero_nota",
    "data_emissao",
    "valor_total",
    "pedido_compra",
    "frs",
    "rf",
    "contrato",
)

# ---------------------------------------------------------------------------
# Extração dos dados do PDF FAT (pdfplumber + regex)
# ---------------------------------------------------------------------------

CNPJS_EMISSOR_VALIDOS = ["19.429.724/0001-83", "04.188.944/0001-95"]
CNPJS_DESTINATARIO_VALIDOS = [
    "33.592.510/0001-54",
    "12.345.678/0001-00",
    "98.765.432/0001-11",
]

def extrair_texto_pdfplumber(caminho_pdf: str) -> str:
    with pdfplumber.open(caminho_pdf) as pdf:
        texto = ""
        for pagina in pdf.pages:
            texto += pagina.extract_text() + "\n"
    return texto

def extrair_cnpj_valido(texto: str, lista_validos: list[str]) -> str | None:
    padrao = r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"
    encontrados = re.findall(padrao, texto)

    lista_validos_limpos = [
        c.replace(".", "").replace("-", "").replace("/", "") for c in lista_validos
    ]

    for cnpj in encontrados:
        cnpj_limpo = cnpj.replace(".", "").replace("-", "").replace("/", "")
        if cnpj_limpo in lista_validos_limpos:
            return cnpj_limpo

    return None

def extrair_numero_nota(caminho_pdf: str) -> str | None:
    nome_arquivo = os.path.basename(caminho_pdf)
    nome_sem_ext = os.path.splitext(nome_arquivo)[0].strip()

    partes = nome_sem_ext.split("_")
    if partes:
        return partes[0]
    return None

def extrair_data_emissao(texto: str) -> str | None:
    padrao = r"Data de Emissão:?\s*(\d{2}/\d{2}/\d{4})"
    encontrados = re.findall(padrao, texto)
    return encontrados[0] if encontrados else None

def extrair_data_vencimento(texto: str) -> str | None:
    padrao = r"\d+/\d+\s+[\d.,]+\s+(\d{2}/\d{2}/\d{4})"
    encontrados = re.findall(padrao, texto)
    return encontrados[0] if encontrados else None

def extrair_valor_total(texto: str) -> str | None:
    padrao = r"Valor Total.*?(\d{1,3}(?:\.\d{3})*,\d{2})"
    encontrados = re.findall(padrao, texto, flags=re.IGNORECASE | re.DOTALL)
    return encontrados[0] if encontrados else None

def extrair_pedido_compra(texto: str) -> str | None:
    padrao = r"(?:Pedido|PEDIDO):?\s*(\d+)"
    encontrados = re.findall(padrao, texto, flags=re.IGNORECASE)
    return encontrados[0] if encontrados else None

def verificar_imposto(texto: str) -> str | None:
    frase_issqn = (
        "Locação de bens móveis não incidente da cobrança de imposto ISSQN "
        "conforme lei federal complementar n° 116, de julho de 2003"
    )
    if re.search(re.escape(frase_issqn), texto, flags=re.IGNORECASE):
        return "não"

    padrao = r"ICMS|PIS|COFINS"
    encontrados = re.findall(padrao, texto, flags=re.IGNORECASE)
    return encontrados[0].lower() if encontrados else None

def extrair_rfs(texto: str) -> str | None:
    padrao = r"(?:FRS|FRs):?\s*(\d+)"
    encontrados = re.findall(padrao, texto, flags=re.IGNORECASE)
    return encontrados[0] if encontrados else None

def extrair_rf(texto: str) -> str | None:
    # RF = Relatório de Faturamento. Na observação do PDF ele vem como
    # "FR:6203231507" ou "RF:..."; o \b e o dígito obrigatório evitam casar
    # com "FRS:" e com o cabeçalho "RF FATURA Nº".
    padrao = r"\b(?:FR|RF)\s*:?\s*(\d+)"
    encontrados = re.findall(padrao, texto)
    return encontrados[0] if encontrados else None

def extrair_contrato(texto: str) -> str | None:
    padrao = r"(?:Contrato|CONTRATO):?\s*(\d+)"
    encontrados = re.findall(padrao, texto, flags=re.IGNORECASE)
    return encontrados[0] if encontrados else None

def converter_data_para_iso(data_br: str) -> str | None:
    # PDF traz "dd/mm/aaaa", mas o campo do formulário (page.py) espera
    # "aaaa-mm-dd".
    if not data_br:
        return None
    return datetime.strptime(data_br, "%d/%m/%Y").strftime("%Y-%m-%d")

def listar_pdfs_fat(pasta: str) -> list[str]:
    # Mesmo critério do nfsvale (verificar_nota em pdf_parser.py): nota de
    # locação é a que termina em "FAT" no nome (ignorando maiúscula/
    # minúscula), diferente das notas de serviço, que terminam em "NF".
    caminhos = []
    for nome_arquivo in sorted(os.listdir(pasta)):
        nome_sem_ext = os.path.splitext(nome_arquivo)[0].strip().upper()
        if nome_sem_ext.endswith("FAT") and nome_arquivo.lower().endswith(".pdf"):
            caminhos.append(os.path.join(pasta, nome_arquivo))
    return caminhos

def extrair_dados_nota_locacao(caminho_pdf: str) -> dict:
    texto = extrair_texto_pdfplumber(caminho_pdf)

    data_emissao_br = extrair_data_emissao(texto)
    data_vencimento_br = extrair_data_vencimento(texto)

    return {
        "cnpj_emissor": extrair_cnpj_valido(texto, CNPJS_EMISSOR_VALIDOS),
        "cnpj_destinatario": extrair_cnpj_valido(texto, CNPJS_DESTINATARIO_VALIDOS),
        "numero_nota": extrair_numero_nota(caminho_pdf),
        "data_emissao_br": data_emissao_br,
        "data_emissao": converter_data_para_iso(data_emissao_br),
        "data_vencimento_br": data_vencimento_br,
        "data_vencimento": converter_data_para_iso(data_vencimento_br),
        "valor_total": extrair_valor_total(texto),
        "possui_imposto": verificar_imposto(texto),
        "pedido_compra": extrair_pedido_compra(texto),
        "frs": extrair_rfs(texto),
        "rf": extrair_rf(texto),
        "contrato": extrair_contrato(texto),
    }


# ---------------------------------------------------------------------------
# Preenchimento do formulário no portal
# ---------------------------------------------------------------------------

def abrir_formulario_outro_documento(page: Page):
    # Em vez de passar pelo menu lateral (hover instável) -> "Adicionar"
    # -> dropdown "Tipo de Processo" -> dropdown "Categoria" -> botão
    # "+ Adicionar" (que abre um popup), vamos direto pra URL do
    # formulário — evita toda a interação frágil com o menu recolhido.
    page.goto(f"{PORTAL_URL}nf/tax_documents/other_invoice/new")
    print("Abriu direto o formulário de 'Outro Documento' pela URL")
    page.wait_for_timeout(ESPERA_MS)

    return page  # sem popup — o formulário abre na mesma aba


def preencher_tipo_documento(form):
    # Depois do upload do PDF o portal mostra um carregamento por cima do
    # formulário, que às vezes passa de 30 s: o click() espera o campo ficar
    # livre, então damos mais tempo em vez de falhar. Campo e opção são
    # achados pelo id do Select2 e pelo texto (o XPath absoluto e a posição
    # do item na lista quebravam com qualquer mudança no portal).
    form.wait_for_load_state("networkidle", timeout=120_000)
    form.locator('[aria-labelledby="select2-tax_document_model-container"]').click(timeout=120_000)
    print("Abriu o dropdown 'Tipo de Documento'")
    form.wait_for_timeout(ESPERA_MS)

    form.locator(".select2-container--open .select2-results__option", has_text="Aluguel").click()
    print("Selecionou 'Aluguel' em 'Tipo de Documento'")
    form.wait_for_timeout(ESPERA_MS)


def subir_pdf_documento(form, dados):
    form.get_by_role("button", name="* PDF do Documento").set_input_files(
        dados["caminho_pdf"]
    )
    print(f"Subiu o PDF do documento: {dados['caminho_pdf']}")
    form.wait_for_timeout(ESPERA_MS)


def preencher_fornecedor(form):
    form.get_by_role("textbox", name="* CNPJ/CPF").click()
    print("Clicou no campo CNPJ/CPF do fornecedor")
    form.wait_for_timeout(ESPERA_MS)

    form.get_by_role("textbox", name="* CNPJ/CPF").fill(CNPJ_EMISSOR)
    print(f"Preencheu o CNPJ do fornecedor: {CNPJ_EMISSOR}")
    form.wait_for_timeout(ESPERA_MS)

    form.get_by_role("textbox", name="* CNPJ/CPF").press("Enter")
    print("Apertou Enter no CNPJ do fornecedor")
    form.wait_for_timeout(ESPERA_MS)
    # Razão social preenche automático depois de escolher o CNPJ.

    form.locator("#supplier_city").get_by_label("", exact=True).click()
    print("Clicou no campo Cidade do fornecedor")
    form.wait_for_timeout(ESPERA_MS)

    busca_cidade = form.locator("input[type='search']").last
    busca_cidade.fill(CIDADE_EMISSOR)
    print(f"Buscou a cidade do fornecedor: {CIDADE_EMISSOR}")
    form.wait_for_timeout(ESPERA_MS)

    busca_cidade.press("Enter")
    print("Apertou Enter na lista de cidades")
    form.wait_for_timeout(ESPERA_MS)

    # busca_cidade.press("Enter")
    # print("Selecionou a cidade na lista")
    # form.wait_for_timeout(ESPERA_MS)


def preencher_tomador(form, dados):
    # Combobox Select2 (igual ao campo de Cidade) — o id real
    # "tax_document_customer_identification_number" fica num <select>
    # escondido. O span com id "select2-...-container" é só o rótulo
    # interno (fica com tamanho 0 quando vazio, por isso não é clicável);
    # o elemento visível/clicável é o pai (role="combobox"), que referencia
    # esse id via aria-labelledby.
    # Por isso não dá pra usar .fill() direto: precisa clicar pra abrir,
    # digitar na caixa de busca que aparece, e confirmar com Enter.
    # Igual à Alíquota do INSS no serviço: o Select2 só é criado quando a
    # página rola até o <select> original — numa janela pequena ele ainda
    # não existe e o clique espera para sempre.
    form.locator("#tax_document_customer_identification_number").scroll_into_view_if_needed()
    form.wait_for_timeout(1000)
    form.locator(
        '[aria-labelledby="select2-tax_document_customer_identification_number-container"]'
    ).click()
    print("Clicou no campo CNPJ do tomador")
    form.wait_for_timeout(ESPERA_MS)

    busca_cnpj_tomador = form.locator("input[type='search']").last
    busca_cnpj_tomador.fill(CNPJ_DESTINATARIO)
    print(f"Buscou o CNPJ do tomador: {CNPJ_DESTINATARIO}")
    form.wait_for_timeout(ESPERA_MS)

    busca_cnpj_tomador.press("Enter")
    print("Apertou Enter no CNPJ do tomador")
    form.wait_for_timeout(ESPERA_MS)


def preencher_documento(form, dados):
    # Esse campo tem um "setter" customizado (data-has-setter="true") que
    # parece ignorar o valor definido via fill() — simulamos digitação de
    # verdade, tecla por tecla, que é o que esse tipo de campo costuma
    # esperar.
    campo_numero = form.locator("#tax_document_number")
    campo_numero.click()
    campo_numero.press_sequentially(dados["numero_nota"])
    campo_numero.press("Tab")  # confirma/perde o foco pra o valor não ser perdido
    print(f"Preencheu o número da nota: {dados['numero_nota']}")
    form.wait_for_timeout(ESPERA_MS)

    form.get_by_role("textbox", name="* Data de Emissão").fill(dados["data_emissao"])
    print(f"Preencheu a data de emissão: {dados['data_emissao']}")
    form.wait_for_timeout(ESPERA_MS)

    form.get_by_role("textbox", name="* Data de Vencimento").fill(
        dados["data_vencimento"]
    )
    print(f"Preencheu a data de vencimento: {dados['data_vencimento']}")
    form.wait_for_timeout(ESPERA_MS)

    # Valor Total vem do PDF (funcs.py) e precisa ser preenchido — o
    # portal não faz isso automático a partir do upload.
    form.get_by_role("textbox", name="* Valor Total").fill(dados["valor_total"])
    print(f"Preencheu o valor total: {dados['valor_total']}")
    form.wait_for_timeout(ESPERA_MS)


def preencher_tributos(form, dados):
    form.locator("#tax_document_iss_retention_0").check()  # ISS retido pelo tomador = Não
    print("Marcou 'ISS retido pelo tomador' = Não")
    form.wait_for_timeout(ESPERA_MS)

    # TODO: "Base de cálculo do ISS = Valor Total" não apareceu explícito
    # na gravação — confirmar se preenche automático ou precisa ação manual.

    # Bases de cálculo de PIS, COFINS, CSLL, IR e INSS = 0
    campos_base_calculo_zerados = [
        "xpath=/html/body/main/div/div/div/div/form/div/div/div/div[1]/div/div[6]/details/div/div/div/div[2]/div/div/div/div[1]/input",
        "xpath=/html/body/main/div/div/div/div/form/div/div/div/div[1]/div/div[6]/details/div/div/div/div[3]/div/div/div/div[1]/input",
        "xpath=/html/body/main/div/div/div/div/form/div/div/div/div[1]/div/div[6]/details/div/div/div/div[4]/div/div/div/div[1]/input",
        "xpath=/html/body/main/div/div/div/div/form/div/div/div/div[1]/div/div[6]/details/div/div/div/div[5]/div/div/div/div[1]/input",
        "xpath=/html/body/main/div/div/div/div/form/div/div/div/div[1]/div/div[6]/details/div/div/div/div[6]/div/div/div/div[1]/input",
    ]
    for indice, seletor in enumerate(campos_base_calculo_zerados, start=1):
        form.locator(seletor).fill("0")
        print(f"Preencheu base de cálculo zerada {indice}/5: 0")
        form.wait_for_timeout(ESPERA_MS)

    form.locator("#tax_document_icms_base_value").fill("0")
    print("Preencheu a base de cálculo do ICMS: 0")
    form.wait_for_timeout(ESPERA_MS)

    form.get_by_role("spinbutton", name="Alíquota do ICMS").fill("0")
    print("Preencheu a alíquota do ICMS: 0")
    form.wait_for_timeout(ESPERA_MS)


def preencher_e_validar(form, locator, valor, nome_campo, tentativas=3):
    # Esses campos também têm "setter" customizado (data-has-setter="true"),
    # igual o campo Número da Nota — .fill() parece atualizar o value do
    # input, mas não o estado interno que o site usa pra validar o envio.
    # Por isso digitamos tecla por tecla e confirmamos com Tab.
    for tentativa in range(1, tentativas + 1):
        locator.click()
        locator.fill("")
        locator.press_sequentially(valor)
        locator.press("Tab")
        print(f"Preencheu o {nome_campo}: {valor}")
        form.wait_for_timeout(ESPERA_MS)

        if locator.input_value() == valor:
            print(f"Confirmou que o {nome_campo} foi preenchido corretamente")
            return

        print(f"O {nome_campo} não ficou com o valor esperado (tentativa {tentativa}/{tentativas}) — tentando de novo")

    raise RuntimeError(
        f"Não consegui preencher o {nome_campo} corretamente após {tentativas} tentativas"
    )


def preencher_pedido_compra(form, dados):
    # FRS e RF têm data-reload-form="forms" no HTML: ao sair do campo, o
    # site dispara uma recarga assíncrona de todo esse painel. Se a
    # resposta demorar e só chegar depois de já termos preenchido outro
    # campo (FRS/RF/contrato), ela sobrescreve a tela com o estado antigo
    # e apaga o que acabamos de digitar — por isso confere tudo nos 4
    # campos de novo no final, depois de dar tempo da recarga assentar.
    campos = [
        (
            form.locator("#tax_document_invoice_items_attributes_0_purchase_order"),
            dados["pedido_compra"],
            "pedido de compra",
        ),
        (
            form.locator("#tax_document_invoice_items_attributes_0_frs"),
            dados["frs"],
            "FRS",
        ),
        (
            form.locator("#tax_document_invoice_items_attributes_0_billing_report_code"),
            dados["fr"],
            "Relatório de Faturamento (RF)",
        ),
        (
            form.locator("#tax_document_invoice_items_attributes_0_contract_number"),
            dados["contrato"],
            "contrato",
        ),
    ]

    for locator, valor, nome_campo in campos:
        preencher_e_validar(form, locator, valor, nome_campo)

    for tentativa_final in range(1, 3):
        form.wait_for_timeout(ESPERA_MS)
        campos_resetados = [
            (locator, valor, nome_campo)
            for locator, valor, nome_campo in campos
            if locator.input_value() != valor
        ]
        if not campos_resetados:
            print("Conferência final: todos os campos do pedido de compra continuam corretos")
            break

        print(
            f"Conferência final {tentativa_final}: a recarga da página apagou "
            f"{len(campos_resetados)} campo(s) — preenchendo de novo"
        )
        for locator, valor, nome_campo in campos_resetados:
            preencher_e_validar(form, locator, valor, nome_campo)


def preencher_metodo_pagamento(form):
    # O campo usa Select2, que esconde um <select> nativo por trás da
    # caixinha visual. Os IDs dos itens da lista visual (li) mudam a cada
    # carregamento da página (sufixo aleatório do Select2), então em vez
    # de navegar pela interface visual, selecionamos direto no <select>
    # real (id="tax_document_cf_payment_method") — mais estável.
    form.locator("#tax_document_cf_payment_method").select_option(
        label="Crédito em Conta", force=True
    )
    print("Selecionou 'Crédito em Conta' em 'Método de Pagamento'")
    form.wait_for_timeout(ESPERA_MS)


def ingressar_documento(form):
    form.wait_for_timeout(10000)  # deixa a recarga do formulário terminar antes do clique
    ingressar_com_confirmacao(form, "Ingressar Documento")


# ---------------------------------------------------------------------------
# Fluxo completo da nota
# ---------------------------------------------------------------------------

def montar_dados_locacao(caminho_pdf):
    """Extrai do PDF e monta o dict que o preenchimento do formulário espera."""
    extraido = extrair_dados_nota_locacao(caminho_pdf)

    faltando = [c for c in CAMPOS_OBRIGATORIOS if not extraido.get(c)]
    if faltando:
        raise DadosInvalidos(f"campos não encontrados no PDF: {', '.join(faltando)}")

    # Vencimento: vale a data do PDF; a conta emissão + 30 dias é só
    # conferência (e fallback se o PDF não trouxer a data).
    emissao = datetime.strptime(extraido["data_emissao"], "%Y-%m-%d")
    calculado = (emissao + timedelta(days=funcs.DIAS_VENCIMENTO)).strftime("%Y-%m-%d")
    vencimento = extraido.get("data_vencimento")
    if not vencimento:
        print(f"   ⚠️  PDF sem vencimento — usando emissão + {funcs.DIAS_VENCIMENTO}d: {calculado}")
        vencimento = calculado
    elif vencimento != calculado:
        print(
            f"   ℹ️  vencimento do PDF ({vencimento}) difere de emissão + "
            f"{funcs.DIAS_VENCIMENTO}d ({calculado}) — usando o do PDF"
        )

    return {
        "numero_nota": extraido["numero_nota"],
        "data_emissao": extraido["data_emissao"],
        "data_vencimento": vencimento,
        "valor_total": extraido["valor_total"],
        "pedido_compra": extraido["pedido_compra"],
        "frs": extraido["frs"],
        "fr": extraido["rf"],  # o formulário chama de "fr" o que a extração chama de "rf"
        "contrato": extraido["contrato"],
        "caminho_pdf": os.path.abspath(caminho_pdf),
    }


def lancar_uma(context, r, enviar):
    """Retorna (status_para_api | None, texto_do_resumo)."""
    try:
        dados = montar_dados_locacao(r["caminho_pdf"])
    except DadosInvalidos as e:
        return STATUS_PULADO, f"pulada: {e}"

    r["frs"], r["rf"] = dados["frs"], dados["fr"]

    form = context.new_page()
    # O formulário recarrega partes da tela (upload do PDF, cidade do
    # fornecedor...) com um carregamento por cima que pode passar de 30 s;
    # cada ação espera o campo ficar livre por até 2 min antes de falhar.
    form.set_default_timeout(120_000)
    r["_aba"] = form
    abrir_formulario_outro_documento(form)
    subir_pdf_documento(form, dados)
    preencher_tipo_documento(form)
    preencher_fornecedor(form)
    preencher_tomador(form, dados)
    preencher_documento(form, dados)
    preencher_tributos(form, dados)
    preencher_pedido_compra(form, dados)
    preencher_metodo_pagamento(form)

    if not enviar:
        return None, "preenchida (não enviada)"

    try:
        ingressar_documento(form)
    except Exception as e:  # noqa: BLE001
        raise EnvioIncerto(str(e).splitlines()[0]) from e
    return concluir_envio(form, r)


def lancar_locacao(context, notas, enviar, planilha):
    """Lança todas as notas FAT da lista. Retorna [(numero_nf, 'locacao', texto)]."""
    return funcs.processar_notas(context, notas, "locacao", lancar_uma, enviar, planilha)
