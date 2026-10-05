"""
Cliente das rotas de notas fiscais da Vale (projeto integration-rm):

- GET  /api/notas-fiscais/vale/pendentes
- POST /api/notas-fiscais/vale/pendentes/processados

Busca, pagina e decodifica o PDF (e XML, quando houver) das notas fiscais
da Vale pendentes no mês corrente, salvando os arquivos em uma pasta local
que serve de entrada para o restante do pipeline (extração em funcs.py +
automação em locacao/lancar_locacao.py / servico/lancar_servico.py). Depois que o robô
tenta lançar cada nota no portal, ele deve avisar o resultado via
`marcar_nota_processada`, para que a própria API pare de devolver notas já
lançadas com sucesso (status "ok"/"duplicado") em chamadas futuras do GET.
"""

import base64
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

VALE_API_BASE_URL = os.getenv("VALE_API_BASE_URL", "").rstrip("/")
VALE_API_TOKEN = os.getenv("VALE_API_TOKEN", "")

MAX_TENTATIVAS_503 = 3
BACKOFF_INICIAL_SEGUNDOS = 2

STATUS_OK = "ok"
STATUS_ERRO = "erro"
STATUS_PULADO = "pulado"
STATUS_DUPLICADO = "duplicado"


class NotasValeApiError(Exception):
    pass


def _headers():
    if not VALE_API_TOKEN:
        raise NotasValeApiError(
            "VALE_API_TOKEN não configurado no .env. Gere o token via "
            "POST /api/gerar_token?usuario=<API_KEY>&email=<email> e cole o "
            "valor recebido por e-mail no .env."
        )
    return {"Authorization": f"Bearer {VALE_API_TOKEN}"}


def _requisitar_com_retry(metodo, path, descricao_erro, **kwargs):
    if not VALE_API_BASE_URL:
        raise NotasValeApiError("VALE_API_BASE_URL não configurado no .env.")

    url = f"{VALE_API_BASE_URL}{path}"
    tentativa = 0

    while True:
        try:
            resposta = requests.request(
                metodo, url, headers=_headers(), timeout=60, **kwargs
            )
        except requests.RequestException as e:
            # Sem conexão / timeout: mesma repetição do 503. O POST de
            # "processados" é um upsert, então repetir é seguro.
            if tentativa >= MAX_TENTATIVAS_503:
                raise NotasValeApiError(
                    f"falha de conexão {descricao_erro}: {type(e).__name__}"
                ) from e
            tentativa += 1
            espera = BACKOFF_INICIAL_SEGUNDOS * (2 ** (tentativa - 1))
            print(
                f"⚠️  Falha de conexão {descricao_erro}, tentativa "
                f"{tentativa}/{MAX_TENTATIVAS_503}, aguardando {espera}s..."
            )
            time.sleep(espera)
            continue

        if resposta.status_code == 200:
            return resposta.json()

        if resposta.status_code == 503 and tentativa < MAX_TENTATIVAS_503:
            tentativa += 1
            espera = BACKOFF_INICIAL_SEGUNDOS * (2 ** (tentativa - 1))
            print(
                f"⚠️  API indisponível (503) {descricao_erro}, tentativa "
                f"{tentativa}/{MAX_TENTATIVAS_503}, aguardando {espera}s..."
            )
            time.sleep(espera)
            continue

        if resposta.status_code == 401:
            raise NotasValeApiError(f"401 - token ausente ou inválido ({descricao_erro})")

        if resposta.status_code == 404:
            raise NotasValeApiError(f"404 - não encontrado ({descricao_erro})")

        if resposta.status_code == 422:
            raise NotasValeApiError(
                f"422 - payload/parâmetro inválido ({descricao_erro}): {resposta.text}"
            )

        raise NotasValeApiError(
            f"Erro inesperado {descricao_erro}: "
            f"{resposta.status_code} - {resposta.text}"
        )


def _buscar_pagina(pagina):
    return _requisitar_com_retry(
        "GET",
        "/api/notas-fiscais/vale/pendentes",
        f"ao buscar página {pagina}",
        params={"pagina": pagina},
    )


def _salvar_arquivos(nota, pasta_destino):
    nome_pdf = nota["nome_pdf_a"]
    caminho_pdf = os.path.join(pasta_destino, nome_pdf)

    with open(caminho_pdf, "wb") as f:
        f.write(base64.b64decode(nota["arquivo_pdf_a"]))

    caminho_xml = None
    if nota.get("arquivo_xml"):
        nome_xml = os.path.splitext(nome_pdf)[0] + ".xml"
        caminho_xml = os.path.join(pasta_destino, nome_xml)
        with open(caminho_xml, "wb") as f:
            f.write(base64.b64decode(nota["arquivo_xml"]))

    return caminho_pdf, caminho_xml


def baixar_notas_pendentes(pasta_destino):
    """
    Busca todas as páginas de notas pendentes da Vale, decodifica e salva
    PDF/XML em `pasta_destino`.

    Retorna uma lista de dicts:
        {"numero_nf", "coligada", "caminho_pdf", "caminho_xml", "erro"}

    Notas com `erro` preenchido pela API não têm arquivo salvo e são
    reportadas na lista de retorno, sem interromper o processamento das
    demais notas da página.
    """
    os.makedirs(pasta_destino, exist_ok=True)

    resultados = []
    pagina = 1

    while True:
        dados = _buscar_pagina(pagina)
        notas = dados.get("notas", [])

        for nota in notas:
            numero_nf = nota.get("numero_nf")
            coligada = nota.get("coligada")

            if not nota.get("erro") and not (
                nota.get("nome_pdf_a") and nota.get("arquivo_pdf_a")
            ):
                nota["erro"] = "API devolveu a nota sem arquivo PDF"

            if nota.get("erro"):
                print(f"⚠️  Nota {numero_nf}: erro reportado pela API - {nota['erro']}")
                resultados.append(
                    {
                        "numero_nf": numero_nf,
                        "coligada": coligada,
                        "caminho_pdf": None,
                        "caminho_xml": None,
                        "erro": nota["erro"],
                    }
                )
                continue

            caminho_pdf, caminho_xml = _salvar_arquivos(nota, pasta_destino)
            resultados.append(
                {
                    "numero_nf": numero_nf,
                    "coligada": coligada,
                    "caminho_pdf": caminho_pdf,
                    "caminho_xml": caminho_xml,
                    "erro": None,
                }
            )

        paginacao = dados.get("paginacao", {})
        if not paginacao.get("tem_proxima_pagina"):
            break
        pagina += 1

    return resultados


def marcar_nota_processada(numero_nf, coligada, status):
    """
    Avisa a API que o robô tentou lançar `numero_nf` (da `coligada`, como
    devolvida pelo GET) no portal da Vale, com
    o resultado em `status` (uma das constantes STATUS_OK / STATUS_ERRO /
    STATUS_PULADO / STATUS_DUPLICADO deste módulo).

    Notas marcadas como STATUS_OK ou STATUS_DUPLICADO deixam de aparecer em
    `baixar_notas_pendentes` daqui pra frente; STATUS_ERRO e STATUS_PULADO
    continuam aparecendo, para nova tentativa.

    A chave da nota é (numero_nf, coligada). Levanta NotasValeApiError com 404
    se essa combinação não existir na base de notas fiscais da API.
    """
    return _requisitar_com_retry(
        "POST",
        "/api/notas-fiscais/vale/pendentes/processados",
        f"ao marcar nota {numero_nf} ({coligada}) como processada",
        json={"numero_nf": numero_nf, "coligada": coligada, "status": status},
    )


if __name__ == "__main__":
    pasta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "entrada")
    resultado = baixar_notas_pendentes(pasta)
    print(f"\n{len(resultado)} nota(s) processada(s), salvas em: {pasta}")
