"""Checkout Paddle exclusivamente em sandbox. Não concede acesso Premium."""
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

PADDLE_API_BASE = "https://sandbox-api.paddle.com"


class ErroPagamento(Exception):
    def __init__(self, mensagem, status=502, resultado_incerto=False):
        super().__init__(mensagem)
        self.status = status
        self.resultado_incerto = resultado_incerto


class SemRedirecionamento(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def configuracao_paddle():
    chave = os.getenv("PADDLE_API_KEY", "")
    token = os.getenv("PADDLE_CLIENT_TOKEN", "")
    preco = os.getenv("PADDLE_PREMIUM_PRICE_ID", "")
    if not chave.startswith("pdl_sdbx_apikey_") or not token.startswith("test_"):
        raise ErroPagamento("Configure as credenciais de sandbox do Paddle no backend.", 503)
    if not re.fullmatch(r"pri_[a-z0-9]{26}", preco):
        raise ErroPagamento("Configure o preço Premium de sandbox no backend.", 503)
    return chave, token, preco


def requisitar_paddle(metodo, caminho, dados=None):
    chave, _, _ = configuracao_paddle()
    requisicao = Request(
        f"{PADDLE_API_BASE}{caminho}",
        method=metodo,
        data=json.dumps(dados).encode() if dados is not None else None,
        headers={"Authorization": f"Bearer {chave}", "Content-Type": "application/json",
                 "Accept": "application/json", "Paddle-Version": "1"},
    )
    try:
        with build_opener(SemRedirecionamento()).open(requisicao, timeout=10) as resposta:
            resultado = json.load(resposta).get("data")
        if not isinstance(resultado, dict):
            raise ValueError("Resposta inválida")
        return resultado
    except HTTPError as erro:
        codigo = ""
        try:
            codigo = json.loads(erro.read()).get("error", {}).get("code", "")
        except (ValueError, AttributeError):
            pass
        if codigo == "transaction_default_checkout_url_not_set":
            mensagem = "Configure o Default payment link em Checkout > Checkout settings no sandbox do Paddle. Para testes locais, tente https://localhost/."
        elif erro.code in (401, 403):
            mensagem = "Confira a chave e as permissões da API de sandbox do Paddle."
        else:
            mensagem = "O Paddle não conseguiu preparar o checkout de teste. Tente novamente."
        raise ErroPagamento(mensagem, resultado_incerto=metodo == "POST" and erro.code >= 500) from None
    except (URLError, TimeoutError, OSError, ValueError, AttributeError):
        raise ErroPagamento(
            "Não foi possível confirmar a resposta do Paddle. Tente consultar novamente em instantes.",
            resultado_incerto=metodo == "POST",
        ) from None


def validar_preco(preco_id):
    preco = requisitar_paddle("GET", f"/prices/{preco_id}")
    if (preco.get("id") != preco_id or preco.get("status") != "active"
            or preco.get("unit_price") != {"amount": "2990", "currency_code": "BRL"}
            or preco.get("billing_cycle") != {"interval": "month", "frequency": 1}
            or preco.get("trial_period") is not None):
        raise ErroPagamento("O preço configurado deve ser Premium ativo de R$ 29,90/mês, sem período gratuito.", 503)


def validar_transacao(transacao, usuario, preco_id):
    itens = transacao.get("items", [])
    if (not re.fullmatch(r"txn_[a-z0-9]{26}", str(transacao.get("id", "")))
            or (transacao.get("custom_data") or {}).get("username") != usuario
            or transacao.get("collection_mode") != "automatic"
            or transacao.get("currency_code") != "BRL"
            or len(itens) != 1 or itens[0].get("quantity") != 1
            or itens[0].get("price", {}).get("id") != preco_id):
        raise ErroPagamento("Não foi possível validar os dados do checkout de teste.")
