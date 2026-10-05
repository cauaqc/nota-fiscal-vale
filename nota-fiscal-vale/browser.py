"""
Sessão/login e ações do portal da Vale que são iguais nos fluxos de
nota_locacao e nota_servico.
"""

import os
import re

from dotenv import load_dotenv
from playwright.sync_api import Page

from api.api_client import STATUS_DUPLICADO, STATUS_OK

load_dotenv()

ESPERA_MS = 3000  # tempo entre cada ação, pra tela ter tempo de responder

PORTAL_URL = "https://vale.virtual360.io/"

# Página do documento criado: /nf/tax_documents/<id numérico>. O formulário em
# branco (.../other_invoice/new, .../service_invoice/new) NÃO casa, porque o
# id é só dígitos.
URL_DOCUMENTO_CRIADO = re.compile(r"/nf/tax_documents/\d+$")


def login(page: Page):
    page.goto(PORTAL_URL)
    print("Abriu o portal da Vale")

    page.get_by_role("link", name="Login Fornecedor").click()
    print("Clicou em 'Login Fornecedor'")

    page.get_by_role("textbox", name="E-mail").fill(os.environ["VALE_PORTAL_EMAIL"])
    print("Preencheu o e-mail")

    page.get_by_role("textbox", name="Senha").fill(os.environ["VALE_PORTAL_SENHA"])
    print("Preencheu a senha")

    page.get_by_role("button", name="Login").click()
    print("Clicou em 'Login'")

    # Popup de boas-vindas cobre a tela inteira — obrigatório fechar antes
    # de continuar, senão bloqueia os campos por trás dele. O "X" é um
    # botão de ícone com aria-label="Fechar" (bi-x-lg).
    page.get_by_role("button", name="Fechar").click(timeout=45000)
    print("Fechou o popup pós-login")


def ingressar_com_confirmacao(form, nome_botao: str):
    """
    Clica no botão final ("Ingressar Documento" / "Ingressar Nota") e espera o
    portal criar o documento.

    O botão tem data-confirm (padrão Rails/UJS): ao clicar, o navegador mostra
    um confirm() nativo e só envia o formulário de verdade se for aceito.
    Descartar (Cancelar) esse diálogo cancela o envio inteiro SEM erro nenhum
    (nenhuma requisição sai e a página não muda) — por isso aceita (OK).
    Ao aceitar, a página navega pra /nf/tax_documents/<id> do documento
    criado; espera essa navegação (até 30 s) em vez de um tempo fixo.
    """

    def tratar_dialogo(dialog):
        print(f"Apareceu a confirmação de envio: \"{dialog.message}\" — aceitando (OK)")
        dialog.accept()

    form.once("dialog", tratar_dialogo)
    form.get_by_role("button", name=nome_botao).click()
    print(f"Clicou em '{nome_botao}'")
    form.wait_for_url(URL_DOCUMENTO_CRIADO, timeout=30000)
    print(f"Documento criado: {form.url}")


# Tela do documento criado: título "#9039322 - Outros Documentos" e, quando o
# portal já tinha uma nota igual, o aviso "Identificamos um registro
# duplicado ... Confira no processo de ID: #8839374".
RE_PROCESSO = re.compile(r"#(\d{4,})\s*-\s*\S")
RE_DUPLICADO = re.compile(r"registro duplicado.*?ID:\s*#(\d+)", re.S | re.I)


def ler_resultado_envio(form):
    """Lê da tela do documento criado: (numero_do_processo, id_do_original_se_duplicado)."""
    form.get_by_text(RE_PROCESSO).first.wait_for(timeout=15000)
    texto = form.inner_text("body")
    processo = RE_PROCESSO.search(texto)
    duplicado = RE_DUPLICADO.search(texto)
    return (
        processo.group(1) if processo else None,
        duplicado.group(1) if duplicado else None,
    )


def concluir_envio(form, r):
    """Chamado logo depois de o portal confirmar a criação. Guarda o nº do processo."""
    try:
        processo, original = ler_resultado_envio(form)
    except Exception as e:  # noqa: BLE001  (o envio JÁ aconteceu: não vira erro)
        print(f"   ⚠️  enviada, mas não consegui ler o nº do processo: {str(e).splitlines()[0]}")
        return STATUS_OK, f"ENVIADA — nº do processo NÃO lido ({form.url})"

    r["processo_vale"] = processo
    if original:
        return (
            STATUS_DUPLICADO,
            f"ENVIADA, mas o portal acusou DUPLICADO (já existia o processo #{original}); novo processo #{processo}",
        )
    return STATUS_OK, f"ENVIADA — processo #{processo}"
