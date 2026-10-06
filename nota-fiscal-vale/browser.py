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


# Campos do formulário que o navegador considera inválidos (obrigatório vazio,
# anexo sumido...). Com algum assim, o clique em "Ingressar" não envia nada.
JS_CAMPOS_INVALIDOS = r"""() => {
  const out = [];
  document.querySelectorAll('form input, form select, form textarea').forEach(function (el) {
    if (el.type === 'hidden' || el.disabled || el.checkValidity()) return;
    const lab = el.id ? document.querySelector('label[for="' + el.id + '"]') : null;
    out.push((lab ? lab.innerText.trim() : el.id) + ': ' + el.validationMessage);
  });
  return out;
}"""


def garantir_anexo(form, seletor: str, caminho: str):
    """
    As recargas automáticas do formulário às vezes perdem um arquivo já
    anexado (visto no PDF da NF 5322). O portal sobe o arquivo na hora e
    esvazia o <input>, então vazio não quer dizer perdido: o sinal é o campo
    ficar inválido ("Selecione um ficheiro"). Nesse caso anexa de novo.
    """
    # O id se repete num <input type=hidden> com a referência do upload:
    # filtra só o campo de arquivo.
    campos = form.locator(f"input[type=file]{seletor}")
    if campos.evaluate_all("els => els.some(el => !el.checkValidity())"):
        print(f"   ⚠️  o anexo '{seletor}' se perdeu nas recargas — anexando de novo")
        campos.first.set_input_files(caminho)
        form.wait_for_timeout(ESPERA_MS)


def conferir_formulario(form, nome_botao: str):
    """
    Pergunta ao navegador se algum campo está inválido ANTES do clique final.
    Se estiver, levanta erro comum (nada foi enviado) em vez de clicar e
    deixar a nota como "envio incerto".
    """
    invalidos = form.evaluate(JS_CAMPOS_INVALIDOS)
    if invalidos:
        raise RuntimeError(f"formulário com campo inválido, não cliquei em '{nome_botao}': {'; '.join(invalidos)}")
    print("Conferiu o formulário: nenhum campo inválido")


def ingressar_com_confirmacao(form, nome_botao: str):
    """
    Clica no botão final ("Ingressar Documento" / "Ingressar Nota") e espera o
    portal criar o documento.

    O botão tem data-confirm (padrão Rails/UJS): ao clicar, o navegador mostra
    um confirm() nativo e só envia o formulário de verdade se for aceito.
    Descartar (Cancelar) esse diálogo cancela o envio inteiro SEM erro nenhum
    (nenhuma requisição sai e a página não muda) — por isso aceita (OK).
    Ao aceitar, a página navega pra /nf/tax_documents/<id> do documento
    criado; espera essa navegação (até 90 s) em vez de um tempo fixo.
    """

    def tratar_dialogo(dialog):
        print(f"Apareceu a confirmação de envio: \"{dialog.message}\" — aceitando (OK)")
        dialog.accept()

    form.once("dialog", tratar_dialogo)
    form.get_by_role("button", name=nome_botao).click()
    print(f"Clicou em '{nome_botao}'")
    form.wait_for_url(URL_DOCUMENTO_CRIADO, timeout=90_000)
    print(f"Documento criado: {form.url}")


PASTA_ERROS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "erros")


def registrar_tela_do_erro(form, numero_nf):
    """
    Guarda o que a aba mostra quando o envio não confirma: print em erros/ e as
    mensagens de erro visíveis do portal. Retorna um texto curto para o resumo.
    """
    try:
        os.makedirs(PASTA_ERROS, exist_ok=True)
        caminho = os.path.join(PASTA_ERROS, f"NF_{numero_nf}.png")
        form.screenshot(path=caminho, full_page=True)
        mensagens = form.locator(
            ".alert:visible, .invalid-feedback:visible, .error:visible, .toast:visible, .flash:visible"
        ).all_inner_texts()
        mensagens = [" ".join(m.split()) for m in mensagens if m.strip()]
        print(f"   🖼️  print da tela: {caminho}")
        print(f"   URL: {form.url}")
        for m in mensagens:
            print(f"   mensagem do portal: {m}")
        return f"URL {form.url}; mensagens: {mensagens or 'nenhuma'}"
    except Exception as e:  # noqa: BLE001
        return f"não consegui registrar a tela: {str(e).splitlines()[0]}"


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
