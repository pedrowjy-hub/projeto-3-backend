"""Cliente Dropbox Sign restrito a documentos de teste. Credencial somente no backend."""
import os
import re
import time
from urllib.parse import urlparse, parse_qs
import requests

BASE_URL = "https://api.hellosign.com/v3"


class ErroAssinatura(Exception):
    def __init__(self, mensagem, status=502, incerto=False):
        super().__init__(mensagem)
        self.status = status
        self.incerto = incerto


def chave_api():
    chave = os.getenv("DROPBOX_SIGN_API_KEY", "").strip()
    if not chave:
        raise ErroAssinatura("Configure DROPBOX_SIGN_API_KEY no backend.", 503)
    return chave


def client_id():
    value = os.getenv("DROPBOX_SIGN_CLIENT_ID", "").strip()
    if not re.fullmatch(r"[a-fA-F0-9]{32,64}", value):
        raise ErroAssinatura("A assinatura dentro da plataforma ainda precisa do Client ID da API App. Peça a configuração ao responsável pela aplicação.", 503)
    return value


def identificador(valor):
    if not isinstance(valor, str) or not re.fullmatch(r"[a-f0-9]{32,64}", valor):
        raise ErroAssinatura("Identificador de assinatura inválido.", 400)
    return valor


def requisitar(method, path, *, data=None, files=None, binary=False):
    try:
        with requests.Session() as session:
            response = session.request(method, BASE_URL + path, auth=(chave_api(), ""),
                data=data, files=files, timeout=(5, 20), allow_redirects=False, stream=binary)
            with response:
                if response.status_code != 200:
                    if response.status_code == 409:
                        raise ErroAssinatura("O documento ainda está sendo preparado. Tente novamente em instantes.", 409)
                    if response.status_code in (401, 403):
                        raise ErroAssinatura("A credencial do Dropbox Sign não foi aceita. Confira o backend.", 503)
                    raise ErroAssinatura("O Dropbox Sign não concluiu a operação. Tente consultar o status mais tarde.",
                        incerto=method == "POST" and (response.status_code >= 500 or response.status_code in (408,)))
                if binary:
                    chunks, total = [], 0
                    for chunk in response.iter_content(65536):
                        total += len(chunk)
                        if total > 12 * 1024 * 1024:
                            raise ErroAssinatura("O PDF final excede o limite de download.")
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    if not content.startswith(b"%PDF-"):
                        raise ErroAssinatura("O provedor não retornou um PDF válido.")
                    return content
                data = response.json()
                if not isinstance(data, dict):
                    raise ErroAssinatura("Resposta inválida do Dropbox Sign.", incerto=method == "POST")
                return data
    except (requests.RequestException, ValueError):
        raise ErroAssinatura("Não foi possível consultar o Dropbox Sign. Confira a conexão.", incerto=method == "POST") from None


def consultar_pedido(request_id):
    data = requisitar("GET", "/signature_request/" + identificador(request_id))
    pedido = data.get("signature_request")
    if not isinstance(pedido, dict) or pedido.get("test_mode") is not True:
        raise ErroAssinatura("Somente assinaturas em modo de teste são permitidas.", 409)
    if pedido.get("signature_request_id") != request_id:
        raise ErroAssinatura("Resposta de assinatura não corresponde ao contrato.")
    return pedido


def enviar_pedido(contrato):
    data = {"title": contrato["titulo"], "subject": "[TESTE ACADÊMICO] " + contrato["titulo"],
        "message": "Demonstração acadêmica. Este documento é enviado exclusivamente em modo de teste.",
        "test_mode": "1", "client_id": contrato["_client_id"], "allow_decline": "1", "allow_reassign": "0",
        "metadata[contrato_id]": contrato["_id"], "metadata[envio_ref]": contrato["_envio_ref"],
        "metadata[sha256]": contrato["sha256"]}
    for index, pessoa in enumerate(contrato["signatarios"]):
        data[f"signers[{index}][email_address]"] = pessoa["email"]
        data[f"signers[{index}][name]"] = pessoa["nome"]
    result = requisitar("POST", "/signature_request/create_embedded", data=data,
        files={"files[0]": ("contrato.pdf", bytes(contrato["_pdf"]), "application/pdf")})
    pedido = result.get("signature_request")
    if not isinstance(pedido, dict):
        raise ErroAssinatura("O provedor não confirmou o pedido. Confira antes de reenviar.", incerto=True)
    return pedido


def url_assinatura(signature_id):
    data = requisitar("GET", "/embedded/sign_url/" + identificador(signature_id))
    embedded = data.get("embedded")
    if not isinstance(embedded, dict):
        raise ErroAssinatura("O provedor não retornou o acesso para assinatura.")
    value = embedded.get("sign_url")
    if not isinstance(value, str):
        raise ErroAssinatura("O acesso para assinatura é inválido.")
    try:
        parsed = urlparse(value)
        port = parsed.port
        query = parse_qs(parsed.query)
    except ValueError:
        raise ErroAssinatura("O acesso para assinatura é inválido.") from None
    expires = embedded.get("expires_at")
    if (parsed.scheme != "https" or parsed.hostname not in {"app.hellosign.com", "app.sign.dropbox.com"}
            or parsed.username or parsed.password or port not in (None, 443)
            or parsed.path != "/editor/embeddedSign" or query.get("signature_id") != [signature_id]
            or not query.get("token") or type(expires) not in (int, float) or expires <= time.time()):
        raise ErroAssinatura("O acesso para assinatura é inválido ou expirou.")
    return {"sign_url": value, "expires_at": expires}


def baixar_pdf(request_id):
    return requisitar("GET", "/signature_request/files/" + identificador(request_id) + "?file_type=pdf", binary=True)
