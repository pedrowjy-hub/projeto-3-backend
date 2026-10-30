import copy
import hashlib
import hmac
import json
import threading
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask_jwt_extended import create_access_token
from bson import ObjectId

import dropbox_sign
from api import app

KEY = "somente-mock-local-nao-e-chave-real"
PDF = b"%PDF-1.4\nDocumento de teste\n%%EOF"
REQUEST_ID = "a" * 40
CHAT_ID = "1" * 24
CLIENT_ID = "d" * 40


class Cursor(list):
    def sort(self, key, direction):
        return Cursor(sorted(self, key=lambda item: item.get(key, ""), reverse=direction < 0))
    def limit(self, number):
        return self[:number]


def matches(doc, query):
    for key, value in query.items():
        if key == "$or":
            if not any(matches(doc, part) for part in value): return False
            continue
        actual = doc.get(key)
        if isinstance(value, dict):
            if "$ne" in value and actual == value["$ne"]: return False
            if "$nin" in value and actual in value["$nin"]: return False
        elif isinstance(actual, list):
            if value not in actual: return False
        elif value != actual: return False
    return True


class Collection:
    def __init__(self, *docs): self.docs = list(copy.deepcopy(docs))
    def find_one(self, query, *_args):
        return copy.deepcopy(next((doc for doc in self.docs if matches(doc, query)), None))
    def find(self, query, *_args): return Cursor(copy.deepcopy([doc for doc in self.docs if matches(doc, query)]))
    def insert_one(self, doc): self.docs.append(copy.deepcopy(doc))
    def update_one(self, query, change):
        for doc in self.docs:
            if matches(doc, query):
                for key, value in change.get("$set", {}).items():
                    target = doc
                    parts = key.split(".")
                    for part in parts[:-1]: target = target.setdefault(part, {})
                    target[parts[-1]] = copy.deepcopy(value)
                for key, value in change.get("$push", {}).items(): doc.setdefault(key, []).append(copy.deepcopy(value))
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


@pytest.fixture
def flow(monkeypatch):
    monkeypatch.setenv("DROPBOX_SIGN_API_KEY", KEY)
    monkeypatch.setenv("DROPBOX_SIGN_CLIENT_ID", CLIENT_ID)
    monkeypatch.setitem(app.config, "JWT_SECRET_KEY", "contratos-chave-isolada-testes-1234567890")
    premium = {"status": "active", "premium_ate": "2099-01-01T00:00:00Z"}
    db = {"tbl_usuarios": Collection(
        {"username": "empresa", "nome": "Marca", "email": "marca@example.test", "papel": "usuario", "tipo_perfil": "empresa", "pagamento": premium},
        {"username": "creator", "nome": "Creator", "email": "creator@example.test", "papel": "usuario", "tipo_perfil": "influenciador", "pagamento": premium},
        {"username": "outro", "nome": "Outra", "email": "outra@example.test", "papel": "usuario", "tipo_perfil": "empresa", "pagamento": premium}),
        "tbl_chats": Collection({"_id": ObjectId(CHAT_ID), "id_empresa": "empresa", "id_influenciador": "creator", "conversa": []}),
        "tbl_contratos": Collection()}
    state = {"posts": [], "get_error": False, "send_error": False, "ready": True}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass
        def answer(self, status, data, pdf=False):
            self.send_response(status)
            self.send_header("Content-Type", "application/pdf" if pdf else "application/json")
            self.end_headers()
            self.wfile.write(data if pdf else json.dumps(data).encode())
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            message = BytesParser(policy=policy.default).parsebytes(b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body)
            fields = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True) for part in message.iter_parts()}
            assert self.path == "/signature_request/create_embedded"
            state["posts"].append(fields)
            state["pedido"] = {"signature_request_id": REQUEST_ID, "test_mode": True,
                "metadata": {key: fields["metadata["+key+"]"].decode() for key in ("contrato_id", "envio_ref", "sha256")},
                "is_complete": False, "is_declined": False, "has_error": False,
                "signatures": [{"signature_id": ("b" if i == 0 else "c") * 40, "signer_email_address": fields[f"signers[{i}][email_address]"].decode(), "status_code": "awaiting_signature"} for i in range(2)]}
            self.answer(500 if state["send_error"] else 200, {"signature_request": state["pedido"]})
        def do_GET(self):
            if state["get_error"]: self.answer(500, {}); return
            if self.path.startswith("/embedded/sign_url/"):
                import time
                signature_id = self.path.rsplit("/", 1)[-1]
                self.answer(200, {"embedded": {"sign_url": "https://app.hellosign.com/editor/embeddedSign?signature_id=" + signature_id + "&token=mock-only", "expires_at": int(time.time()) + 3600}})
            elif self.path.startswith("/signature_request/files/"):
                self.answer(200 if state["ready"] else 409, PDF, pdf=True)
            else: self.answer(200, {"signature_request": state["pedido"]})
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    monkeypatch.setattr(dropbox_sign, "BASE_URL", f"http://127.0.0.1:{server.server_port}")
    def headers(username="empresa"):
        with app.app_context(): return {"Authorization": "Bearer " + create_access_token(identity=username)}
    state.update(client=app.test_client(), db=db, headers=headers)
    with patch("api.conectar_db", return_value=db): yield state
    server.shutdown(); server.server_close(); thread.join()


def create(flow, **fields):
    return flow["client"].post("/contratos", data={"titulo": "Parceria teste", "chat_id": CHAT_ID, "documento": (BytesIO(PDF), "acordo.pdf"), **fields}, headers=flow["headers"]())


def approve(flow, contract_id):
    contract = flow["db"]["tbl_contratos"].find_one({"_id": contract_id})
    for username in ("empresa", "creator"):
        response = flow["client"].post(f"/contratos/{contract_id}/aprovar", json={"sha256": contract["sha256"]}, headers=flow["headers"](username))
        assert response.status_code == 200, response.json


def send(flow):
    contract_id = create(flow).json["id"]
    approve(flow, contract_id)
    response = flow["client"].post(f"/contratos/{contract_id}/enviar", json={}, headers=flow["headers"]())
    return contract_id, response


def webhook(flow, event_type="signature_request_all_signed", valid=True, **data):
    timestamp = "1793360000"
    signature = hmac.new(KEY.encode(), (timestamp+event_type).encode(), hashlib.sha256).hexdigest()
    payload = {"event": {"event_time": timestamp, "event_type": event_type, "event_hash": signature if valid else "invalid"},
               "signature_request": {"signature_request_id": REQUEST_ID, **data}}
    return flow["client"].post("/webhooks/assinaturas", data={"json": json.dumps(payload)})


def test_rascunho_privado_e_envio_somente_teste(flow):
    cid, response = send(flow)
    assert response.status_code == 200
    assert response.json["status"] == "aguardando_assinaturas"
    sent = flow["posts"][0]
    assert sent["test_mode"] == b"1"
    assert sent["files[0]"] == PDF
    assert sent["signers[0][email_address]"] == b"marca@example.test"
    assert "_pdf" not in response.json and "_envio_ref" not in response.json and "_dropbox_id" not in response.json
    listing = flow["client"].get("/contratos", headers=flow["headers"]()).json
    assert listing[0]["id"] == cid and len(listing) == 1
    assert flow["client"].get("/contratos", headers=flow["headers"]("outro")).json == []
    assert flow["client"].get(f"/contratos/{cid}/documento", headers=flow["headers"]("outro")).status_code == 404


def test_nao_reenvia_convite_duplicado(flow):
    cid, response = send(flow)
    assert response.status_code == 200
    assert flow["client"].post(f"/contratos/{cid}/enviar", json={}, headers=flow["headers"]()).status_code == 200
    assert len(flow["posts"]) == 1


def test_resposta_perdida_bloqueia_reenvio_e_webhook_recupera_vinculo(flow):
    flow["send_error"] = True
    cid, response = send(flow)
    assert response.status_code == 502
    assert flow["db"]["tbl_contratos"].docs[0]["status"] == "revisao_necessaria"
    assert flow["client"].post(f"/contratos/{cid}/enviar", json={}, headers=flow["headers"]()).status_code == 409
    assert len(flow["posts"]) == 1
    assert webhook(flow).status_code == 200
    assert flow["db"]["tbl_contratos"].docs[0]["_dropbox_id"] == REQUEST_ID


def test_webhook_consulta_provedor_nao_confia_no_status_do_payload(flow):
    send(flow)
    assert webhook(flow, is_complete=True).status_code == 200
    assert flow["db"]["tbl_contratos"].docs[0]["status"] == "aguardando_assinaturas"
    assert webhook(flow, valid=False).status_code == 401
    flow["pedido"]["signatures"][0]["status_code"] = "signed"
    assert webhook(flow, "signature_request_signed").status_code == 200
    assert flow["db"]["tbl_contratos"].docs[0]["status"] == "parcialmente_assinado"
    flow["pedido"]["signatures"][1]["status_code"] = "signed"
    flow["pedido"]["is_complete"] = True
    assert webhook(flow).data == b"Hello API Event Received"
    assert webhook(flow).status_code == 200
    assert flow["db"]["tbl_contratos"].docs[0]["status"] == "assinado"


def test_download_so_apos_assinatura_completa_e_pdf_preparado(flow):
    cid, _ = send(flow)
    endpoint = f"/contratos/{cid}/documento"
    assert flow["client"].get(endpoint, headers=flow["headers"]()).data == PDF
    assert flow["client"].get(endpoint+"?tipo=assinado", headers=flow["headers"]()).status_code == 409
    flow["pedido"]["is_complete"] = True
    for item in flow["pedido"]["signatures"]: item["status_code"] = "signed"
    flow["ready"] = False
    assert flow["client"].get(endpoint+"?tipo=assinado", headers=flow["headers"]()).status_code == 409
    flow["ready"] = True
    result = flow["client"].get(endpoint+"?tipo=assinado", headers=flow["headers"]("creator"))
    assert result.data == PDF and result.headers["Cache-Control"] == "private, no-store"


def test_producao_e_troca_de_signatario_bloqueadas(flow):
    cid, _ = send(flow)
    endpoint = f"/contratos/{cid}/sincronizar"
    flow["pedido"]["test_mode"] = False
    assert flow["client"].post(endpoint, json={}, headers=flow["headers"]()).status_code == 409
    flow["pedido"]["test_mode"] = True
    flow["pedido"]["signatures"][0]["signer_email_address"] = "estranho@example.test"
    assert flow["client"].post(endpoint, json={}, headers=flow["headers"]()).status_code == 502


@pytest.mark.parametrize("username", ["empresa", "creator"])
def test_premium_revalidado_no_envio(flow, username):
    cid = create(flow).json["id"]
    approve(flow, cid)
    user = next(item for item in flow["db"]["tbl_usuarios"].docs if item["username"] == username)
    user["pagamento"]["premium_ate"] = "2000-01-01T00:00:00Z"
    assert flow["client"].post(f"/contratos/{cid}/enviar", json={}, headers=flow["headers"]()).status_code == 403
    assert not flow["posts"]


def test_mudanca_de_email_exige_nova_revisao(flow):
    cid = create(flow).json["id"]
    approve(flow, cid)
    flow["db"]["tbl_usuarios"].docs[1]["email"] = "novo@example.test"
    assert flow["client"].post(f"/contratos/{cid}/enviar", json={}, headers=flow["headers"]()).status_code == 409
    assert not flow["posts"]


def test_upload_e_identidades_nao_controladas_por_cliente(flow):
    assert create(flow, email="alheio@example.test").status_code == 400
    assert create(flow, parceiro="outro").status_code == 400
    assert create(flow, documento=(BytesIO(b"nao PDF"), "arquivo.pdf")).status_code == 400
    assert create(flow, documento=(BytesIO(b"%PDF-"+b"x"*(5*1024*1024)), "grande.pdf")).status_code == 400
    assert not flow["db"]["tbl_contratos"].docs


def test_sem_login_ou_criador_incorreto_nao_envia(flow):
    assert flow["client"].get("/contratos").status_code == 401
    cid = create(flow).json["id"]
    assert flow["client"].post(f"/contratos/{cid}/enviar", json={}, headers=flow["headers"]("outro")).status_code == 404
    assert not flow["posts"]


def test_pedido_incompleto_nao_conclui_e_consulta_antiga_nao_reabre(flow):
    cid, _ = send(flow)
    flow["pedido"]["is_complete"] = True
    assert webhook(flow).status_code == 200
    assert flow["db"]["tbl_contratos"].docs[0]["status"] != "assinado"
    for item in flow["pedido"]["signatures"]: item["status_code"] = "signed"
    assert webhook(flow).status_code == 200
    for item in flow["pedido"]["signatures"]: item["status_code"] = "awaiting_signature"
    flow["pedido"]["is_complete"] = False
    assert webhook(flow).status_code == 200
    assert flow["db"]["tbl_contratos"].docs[0]["status"] == "assinado"


def test_chave_ausente_nao_reserva_envio_e_provedor_indisponivel_nao_confirma(flow, monkeypatch):
    cid = create(flow).json["id"]
    approve(flow, cid)
    monkeypatch.delenv("DROPBOX_SIGN_API_KEY")
    assert flow["client"].post(f"/contratos/{cid}/enviar", json={}, headers=flow["headers"]()).status_code == 503
    assert flow["db"]["tbl_contratos"].docs[0]["status"] == "rascunho"
    monkeypatch.setenv("DROPBOX_SIGN_API_KEY", KEY)
    monkeypatch.setenv("DROPBOX_SIGN_CLIENT_ID", CLIENT_ID)
    assert flow["client"].post(f"/contratos/{cid}/enviar", json={}, headers=flow["headers"]()).status_code == 200
    flow["get_error"] = True
    assert flow["client"].post(f"/contratos/{cid}/sincronizar", json={}, headers=flow["headers"]()).status_code == 502
    assert flow["db"]["tbl_contratos"].docs[0]["status"] == "aguardando_assinaturas"


def test_notificacao_invalida_e_teste_de_callback(flow):
    assert flow["client"].post("/webhooks/assinaturas", data={"json": "invalido"}).status_code == 400
    assert webhook(flow, "callback_test").data == b"Hello API Event Received"
    # Eventos fora do fluxo implementado não desativam o callback por erros/retries.
    flow["get_error"] = True
    assert webhook(flow, "signature_request_canceled").status_code == 200
    assert not flow["posts"]


def test_contrato_exige_chat_e_duas_aprovacoes_do_mesmo_hash(flow):
    assert create(flow, chat_id="2" * 24).status_code == 404
    cid = create(flow).json["id"]
    endpoint = f"/contratos/{cid}"
    assert flow["client"].post(endpoint+"/enviar", json={}, headers=flow["headers"]()).status_code == 409
    doc = flow["db"]["tbl_contratos"].docs[0]
    assert doc["chat_id"] == CHAT_ID
    assert flow["client"].post(endpoint+"/aprovar", json={"sha256": "errado"}, headers=flow["headers"]()).status_code == 409
    assert flow["client"].post(endpoint+"/aprovar", json={"sha256": doc["sha256"]}, headers=flow["headers"]()).status_code == 200
    assert set(doc["aprovacoes"]) == {"empresa"}
    assert flow["client"].post(endpoint+"/enviar", json={}, headers=flow["headers"]()).status_code == 409
    approve(flow, cid)
    assert flow["client"].post(endpoint+"/enviar", json={}, headers=flow["headers"]("creator")).status_code == 200
    assert flow["posts"][0]["client_id"] == CLIENT_ID.encode()


def test_assinatura_incorporada_libera_apenas_url_do_signatario_autenticado(flow):
    cid, _ = send(flow)
    endpoint = f"/contratos/{cid}/assinar"
    empresa = flow["client"].post(endpoint, json={}, headers=flow["headers"]())
    creator = flow["client"].post(endpoint, json={}, headers=flow["headers"]("creator"))
    assert empresa.status_code == creator.status_code == 200
    assert "signature_id=" + "b" * 40 in empresa.json["sign_url"]
    assert "signature_id=" + "c" * 40 in creator.json["sign_url"]
    assert empresa.json["test_mode"] is True
    assert empresa.json["client_id"] == CLIENT_ID
    assert empresa.headers["Cache-Control"] == "private, no-store"
    assert flow["client"].post(endpoint, json={"signature_id": "b" * 40}, headers=flow["headers"]("creator")).status_code == 400
    assert flow["client"].post(endpoint, json={}, headers=flow["headers"]("outro")).status_code == 404
    flow["pedido"]["signatures"][0]["status_code"] = "signed"
    assert flow["client"].post(endpoint, json={}, headers=flow["headers"]()).status_code == 409


def test_falta_client_id_nao_envia_email_nem_reserva_contrato(flow, monkeypatch):
    cid = create(flow).json["id"]
    approve(flow, cid)
    monkeypatch.delenv("DROPBOX_SIGN_CLIENT_ID")
    response = flow["client"].post(f"/contratos/{cid}/enviar", json={}, headers=flow["headers"]())
    assert response.status_code == 503
    assert not flow["posts"]
    assert flow["db"]["tbl_contratos"].docs[0]["status"] == "rascunho"


def test_chat_nao_permite_forjar_remetente_nem_aprovar_por_texto(flow):
    cid = create(flow).json["id"]
    endpoint = f"/conversas/{CHAT_ID}/mensagens"
    assert flow["client"].post(endpoint, json={"mensagem": "aceito", "remetente_id": "creator"}, headers=flow["headers"]()).status_code == 400
    response = flow["client"].post(endpoint, json={"mensagem": "aceito"}, headers=flow["headers"]())
    assert response.status_code == 201 and response.json["remetente_id"] == "empresa"
    assert flow["db"]["tbl_contratos"].find_one({"_id": cid})["aprovacoes"] == {}
    assert flow["client"].get(endpoint, headers=flow["headers"]("creator")).json["mensagens"][0]["mensagem"] == "aceito"
    assert flow["client"].get(endpoint, headers=flow["headers"]("outro")).status_code == 404
    assert flow["client"].get("/conversas", headers=flow["headers"]("outro")).json == []
    assert flow["client"].get("/conversas", headers=flow["headers"]()).json[0]["id"] == CHAT_ID


def test_abrir_conversa_idempotente_e_contrato_sem_vinculo_nao_assina(flow):
    response = flow["client"].post("/conversas", json={"parceiro": "creator"}, headers=flow["headers"]())
    assert response.status_code == 200 and response.json["id"] == CHAT_ID
    assert len(flow["db"]["tbl_chats"].docs) == 1
    cid, _ = send(flow)
    flow["db"]["tbl_contratos"].docs[0]["chat_id"] = "2" * 24
    assert flow["client"].post(f"/contratos/{cid}/assinar", json={}, headers=flow["headers"]()).status_code == 404
