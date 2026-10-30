"""Eventos Paddle assinados e API Flask reais contra um provedor HTTP local."""
import copy
import hashlib
import hmac
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask_jwt_extended import create_access_token
from pymongo.errors import DuplicateKeyError, PyMongoError

import pagamentos
from api import app, assinatura_premium_ativa
from assinaturas import premium_ativo, iso, campos_assinatura, CAMPOS_LEGADOS

PRICE = "pri_" + "a" * 26
TXN = "txn_" + "b" * 26
SUB = "sub_" + "c" * 26
CUSTOMER = "ctm_" + "d" * 26
SECRET = "segredo-exclusivo-webhook-local"


def matches(doc, query):
    for key, expected in query.items():
        parts = key.split(".")
        current = doc
        for part in parts[:-1]:
            current = current.get(part, {}) if isinstance(current, dict) else {}
        value_at_key = current.get(parts[-1]) if isinstance(current, dict) else None
        exists = isinstance(current, dict) and parts[-1] in current
        if key == "$or":
            if not any(matches(doc, part) for part in expected):
                return False
        elif isinstance(expected, dict):
            for op, value in expected.items():
                if op == "$exists" and exists != value:
                    return False
                if op == "$lte" and (value_at_key is None or value_at_key > value):
                    return False
        elif value_at_key != expected:
            return False
    return True


class Collection:
    def __init__(self, *docs):
        self.docs = list(copy.deepcopy(docs))
        self.fail_once = False

    def find_one(self, query, _projection=None):
        return copy.deepcopy(next((doc for doc in self.docs if matches(doc, query)), None))

    def insert_one(self, doc):
        if "_id" in doc and self.find_one({"_id": doc["_id"]}):
            raise DuplicateKeyError("duplicate")
        self.docs.append(copy.deepcopy(doc))

    def update_one(self, query, change):
        if self.fail_once:
            self.fail_once = False
            raise PyMongoError("falha simulada")
        for doc in self.docs:
            if matches(doc, query):
                doc.update(copy.deepcopy(change.get("$set", {})))
                for key in change.get("$unset", {}):
                    doc.pop(key, None)
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)

    def find_one_and_update(self, query, change):
        previous = self.find_one(query)
        if previous is not None:
            self.update_one(query, change)
        return previous

    def delete_one(self, query):
        self.docs = [doc for doc in self.docs if not matches(doc, query)]


@pytest.fixture
def flow(monkeypatch):
    monkeypatch.setenv("PADDLE_API_KEY", "pdl_sdbx_apikey_somente_teste")
    monkeypatch.setenv("PADDLE_CLIENT_TOKEN", "test_somente_teste")
    monkeypatch.setenv("PADDLE_PREMIUM_PRICE_ID", PRICE)
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setitem(app.config, "JWT_SECRET_KEY", "segredo-teste-assinaturas-nao-usar-1234567890")
    end = datetime.now(timezone.utc) + timedelta(days=30)
    price = {"id": PRICE, "unit_price": {"amount": "2990", "currency_code": "BRL"},
             "billing_cycle": {"interval": "month", "frequency": 1}}
    metadata = {"username": "ana", "checkout_reference": "ref-inicial"}
    state = {"calls": [], "get_error": False, "cancel_error": False,
             "subscription": {"id": SUB, "customer_id": CUSTOMER, "status": "active",
                 "currency_code": "BRL", "collection_mode": "automatic", "custom_data": metadata,
                 "items": [{"quantity": 1, "price": price}],
                 "scheduled_change": None, "next_billed_at": iso(end),
                 "current_billing_period": {"starts_at": iso(end - timedelta(days=30)), "ends_at": iso(end)}},
             "transaction": {"id": TXN, "subscription_id": SUB, "customer_id": CUSTOMER, "status": "completed",
                 "currency_code": "BRL", "collection_mode": "automatic", "custom_data": metadata,
                 "items": [{"quantity": 1, "price": price}],
                 "billing_period": {"starts_at": iso(end - timedelta(days=30)), "ends_at": iso(end)}}}
    db = {"tbl_usuarios": Collection({"username": "ana", "papel": "usuario", "tipo_perfil": "empresa", "assinatura": False}),
          "tbl_checkout_pagamentos": Collection({"_id": "ana", "reference": "ref-inicial", "environment": "sandbox", "transaction_id": TXN}),
          "tbl_eventos_pagamentos": Collection(), "tbl_chats": Collection()}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            state["calls"].append(("GET", self.path, None))
            if state["get_error"]:
                self.answer(500, {"error": {"code": "internal_error"}})
                return
            if self.path.startswith("/subscriptions/"):
                self.answer(200, {"data": state["subscription"]})
            elif self.path.startswith("/transactions/"):
                self.answer(200, {"data": state["transaction"]})
            else:
                self.answer(501, {})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["calls"].append(("POST", self.path, body))
            if self.path != f"/subscriptions/{SUB}/cancel":
                self.answer(501, {})
                return
            state["subscription"]["scheduled_change"] = {"action": "cancel", "effective_at": iso(end)}
            if state["cancel_error"]:
                state["cancel_error"] = False
                self.answer(500, {"error": {"code": "internal_error"}})
                return
            self.answer(200, {"data": state["subscription"]})

        def answer(self, code, body):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    monkeypatch.setattr(pagamentos, "PADDLE_API_BASE", f"http://127.0.0.1:{server.server_port}")
    with app.app_context():
        token = create_access_token(identity="ana")
        other_token = create_access_token(identity="outro")
    state.update(db=db, client=app.test_client(), headers={"Authorization": f"Bearer {token}"},
                 other_headers={"Authorization": f"Bearer {other_token}"})
    with patch("api.conectar_db", return_value=db):
        yield state
    server.shutdown()
    server.server_close()
    thread.join()


def send(flow, event_type="transaction.completed", event_number=1, timestamp=None, **kwargs):
    identifier = TXN if event_type.startswith("transaction.") else SUB
    event = {"event_id": "evt_" + str(event_number).zfill(26), "event_type": event_type,
             "occurred_at": "2026-10-30T00:00:00Z", "data": {"id": identifier}}
    event.update(kwargs)
    raw = json.dumps(event, separators=(",", ":")).encode()
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    signature = hmac.new(SECRET.encode(), timestamp.encode() + b":" + raw, hashlib.sha256).hexdigest()
    return flow["client"].post("/webhooks/pagamentos", data=raw, content_type="application/json",
        headers={"Paddle-Signature": f"ts={timestamp};h1={signature}"})


def user(flow):
    account = flow["db"]["tbl_usuarios"].find_one({"username": "ana"})
    return {**account, **campos_assinatura(account)}


@pytest.mark.parametrize("profile", ["empresa", "influenciador"])
def test_primeiro_pagamento_assinado_libera_acesso(flow, profile):
    flow["db"]["tbl_usuarios"].docs[0]["tipo_perfil"] = profile
    assert send(flow).status_code == 200
    account = user(flow)
    assert account["premium_ate"] == flow["transaction"]["billing_period"]["ends_at"]
    assert assinatura_premium_ativa(account)
    assert account["paddle_subscription_id"] == SUB
    assert account["renovacao_automatica"] is True
    assert flow["client"].get("/me", headers=flow["headers"]).json["assinatura"] is True


def test_duplicado_nao_soma_dias_nem_refaz_consultas(flow):
    assert send(flow).status_code == 200
    before, calls = user(flow)["premium_ate"], len(flow["calls"])
    assert send(flow).json["status"] == "duplicate"
    assert user(flow)["premium_ate"] == before
    assert len(flow["calls"]) == calls


def test_renovacao_usa_periodo_exato_mes_curto_e_evento_antigo_nao_regride(flow):
    assert send(flow).status_code == 200
    original = copy.deepcopy(flow["transaction"])
    flow["transaction"]["id"] = "txn_" + "e" * 26
    flow["transaction"]["custom_data"] = None
    flow["transaction"]["billing_period"] = {"starts_at": "2030-01-31T12:30:00Z", "ends_at": "2030-02-28T12:30:00Z"}
    assert send(flow, event_number=2, data={"id": flow["transaction"]["id"]}).status_code == 200
    assert user(flow)["premium_ate"] == "2030-02-28T12:30:00Z"
    flow["transaction"] = original
    assert send(flow, event_number=3).status_code == 200
    assert user(flow)["premium_ate"] == "2030-02-28T12:30:00Z"


def test_recusa_inicial_nao_libera_premium(flow):
    flow["transaction"]["status"] = "past_due"
    flow["subscription"]["status"] = "past_due"
    assert send(flow, "transaction.payment_failed").status_code == 200
    assert not assinatura_premium_ativa(user(flow))
    assert user(flow)["falha_pagamento"] is True


def test_primeira_recusa_sem_assinatura_criada_nao_causa_reenvios_inuteis(flow):
    flow["transaction"]["status"] = "draft"
    flow["transaction"]["subscription_id"] = None
    assert send(flow, "transaction.payment_failed").json["status"] == "ignored"
    assert send(flow, "transaction.payment_failed").json["status"] == "duplicate"
    assert not user(flow)["assinatura"]
    assert not any("/subscriptions/" in call[1] for call in flow["calls"])


def test_falha_renovacao_preserva_somente_periodo_pago(flow):
    assert send(flow).status_code == 200
    paid_until = user(flow)["premium_ate"]
    flow["transaction"]["status"] = "past_due"
    flow["subscription"]["status"] = "past_due"
    flow["transaction"]["billing_period"]["ends_at"] = "2035-01-01T00:00:00Z"
    assert send(flow, "transaction.payment_failed", 2).status_code == 200
    assert user(flow)["premium_ate"] == paid_until
    assert assinatura_premium_ativa(user(flow))
    after_expiry = datetime.fromisoformat(paid_until.replace("Z", "+00:00")) + timedelta(seconds=1)
    assert not premium_ativo(user(flow), after_expiry)


def test_evento_de_falha_antigo_consulta_estado_atual(flow):
    assert send(flow).status_code == 200
    assert send(flow, "transaction.payment_failed", 2).status_code == 200
    assert not user(flow)["falha_pagamento"]


def test_evento_assinatura_antes_da_transacao_tambem_confere_pagamento(flow):
    assert send(flow, "subscription.created").status_code == 200
    assert assinatura_premium_ativa(user(flow))


def test_atualizacao_de_assinatura_nao_estende_validade_sem_fatura_paga(flow):
    assert send(flow).status_code == 200
    before = user(flow)["premium_ate"]
    flow["subscription"]["current_billing_period"]["ends_at"] = "2035-01-01T00:00:00Z"
    assert send(flow, "subscription.updated", 2).status_code == 200
    assert user(flow)["premium_ate"] == before


@pytest.mark.parametrize("timestamp", [0, 1, 999999999999])
def test_timestamp_expirado_ou_futuro_rejeitado(flow, timestamp):
    assert send(flow, timestamp=timestamp).status_code == 401
    assert not flow["calls"]


def test_corpo_modificado_e_falta_assinatura_rejeitados(flow):
    response = flow["client"].post("/webhooks/pagamentos", json={"event_id": "forjado"})
    assert response.status_code == 401
    assert not flow["calls"]
    raw = b'{"event_type":"transaction.completed"}'
    timestamp = str(int(time.time()))
    signature = hmac.new(SECRET.encode(), timestamp.encode() + b":" + raw, hashlib.sha256).hexdigest()
    response = flow["client"].post("/webhooks/pagamentos", data=raw + b' ',
        headers={"Paddle-Signature": f"ts={timestamp};h1={signature}"})
    assert response.status_code == 401


def test_segredo_ausente_e_credencial_live_bloqueiam(flow, monkeypatch):
    monkeypatch.delenv("PADDLE_WEBHOOK_SECRET")
    assert send(flow).status_code == 503
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("PADDLE_API_KEY", "pdl_live_apikey_nao_usar")
    assert send(flow).status_code == 503
    assert not flow["calls"]


def test_evento_irrelevante_ack_sem_consultar_provedor(flow):
    assert send(flow, "customer.updated").json["status"] == "ignored"
    assert not flow["calls"]


def test_falsificacao_de_custom_data_nao_vincula_transacao_alheia(flow):
    flow["transaction"]["custom_data"]["checkout_reference"] = "outra-referencia"
    # O envelope tem assinatura válida, mas a vinculação não corresponde à criada no servidor.
    assert send(flow).json["status"] == "ignored"
    assert not user(flow)["assinatura"]


def test_preco_alterado_bloqueia_concessao(flow):
    flow["transaction"]["items"][0]["price"]["unit_price"]["amount"] = "1"
    assert send(flow).status_code == 400
    assert not user(flow)["assinatura"]


def test_provedor_falha_e_reenvio_pode_concluir(flow):
    flow["get_error"] = True
    assert send(flow).status_code == 502
    assert not flow["db"]["tbl_eventos_pagamentos"].docs[0]["processed"]
    flow["get_error"] = False
    assert send(flow).status_code == 200
    assert assinatura_premium_ativa(user(flow))


def test_falha_ao_marcar_evento_nao_duplica_periodo_no_reenvio(flow):
    flow["db"]["tbl_eventos_pagamentos"].fail_once = True
    assert send(flow).status_code == 503
    before = user(flow)["premium_ate"]
    assert send(flow).status_code == 200
    assert user(flow)["premium_ate"] == before


def test_reserva_concorrente_responde_retry_sem_conceder(flow):
    flow["db"]["tbl_usuarios"].docs[0]["_paddle_lock_until"] = datetime.now(timezone.utc) + timedelta(minutes=1)
    assert send(flow).status_code == 503
    assert not user(flow)["assinatura"]


def cancel(flow, **kwargs):
    return flow["client"].post("/usuarios/me/assinatura/cancelamento", json={}, headers=flow["headers"], **kwargs)


def test_cancela_no_fim_do_periodo_preservando_acesso_e_sem_post_duplicado(flow):
    assert send(flow).status_code == 200
    before = user(flow)["premium_ate"]
    response = cancel(flow)
    assert response.status_code == 200
    assert response.json["cancelamento_agendado"] is True
    assert response.json["assinatura"] is True
    assert user(flow)["premium_ate"] == before
    assert not user(flow)["renovacao_automatica"]
    assert cancel(flow).status_code == 200
    posts = [call for call in flow["calls"] if call[0] == "POST"]
    assert len(posts) == 1
    assert posts[0][2] == {"effective_from": "next_billing_period"}


def test_cancelamento_com_resposta_perdida_consulta_provedor_antes_de_repetir(flow):
    assert send(flow).status_code == 200
    flow["cancel_error"] = True
    assert cancel(flow).status_code == 502
    assert cancel(flow).status_code == 200
    assert len([call for call in flow["calls"] if call[0] == "POST"]) == 1


def test_cancelamento_nao_aceita_assinatura_de_outro_usuario(flow):
    response = flow["client"].post("/usuarios/me/assinatura/cancelamento",
        json={"subscription_id": SUB}, headers=flow["headers"])
    assert response.status_code == 400
    response = flow["client"].post("/usuarios/me/assinatura/cancelamento", json={}, headers=flow["other_headers"])
    assert response.status_code == 401
    assert not flow["calls"]


def test_cancelamento_confere_cliente_no_provedor(flow):
    assert send(flow).status_code == 200
    flow["subscription"]["customer_id"] = "ctm_" + "x" * 26
    assert cancel(flow).status_code == 403
    assert not [call for call in flow["calls"] if call[0] == "POST"]


def test_expiracao_timestamp_bloqueia_me_e_chat_mesmo_que_data_legada_futura(flow):
    expired = iso(datetime.now(timezone.utc) - timedelta(seconds=1))
    flow["db"]["tbl_usuarios"].docs[0].update(assinatura=True, premium_ate=expired, expiracao_assinatura="31-12-2099")
    assert flow["client"].get("/me", headers=flow["headers"]).json["assinatura"] is False
    assert flow["client"].get("/chats/ana/empresa", headers=flow["headers"]).status_code == 403


def test_periodo_termina_no_instante_exato_com_fuso():
    account = {"assinatura": True, "premium_ate": "2030-02-28T09:30:00-03:00"}
    assert premium_ativo(account, datetime(2030, 2, 28, 12, 29, 59, tzinfo=timezone.utc))
    assert not premium_ativo(account, datetime(2030, 2, 28, 12, 30, tzinfo=timezone.utc))


def test_pausa_e_retomada_nao_acrescentam_periodo(flow):
    assert send(flow).status_code == 200
    before = user(flow)["premium_ate"]
    flow["subscription"]["status"] = "paused"
    assert send(flow, "subscription.paused", 2).status_code == 200
    assert not assinatura_premium_ativa(user(flow))
    flow["subscription"]["status"] = "active"
    assert send(flow, "subscription.resumed", 3).status_code == 200
    assert assinatura_premium_ativa(user(flow))
    assert user(flow)["premium_ate"] == before


def test_cancelamento_efetivado_e_periodo_expirado_nao_reabrem_acesso(flow):
    assert send(flow).status_code == 200
    expired = iso(datetime.now(timezone.utc) - timedelta(seconds=1))
    flow["db"]["tbl_usuarios"].docs[0]["pagamento"]["premium_ate"] = expired
    flow["subscription"]["status"] = "canceled"
    flow["subscription"]["current_billing_period"] = None
    assert send(flow, "subscription.canceled", 2).status_code == 200
    assert not assinatura_premium_ativa(user(flow))
    assert not user(flow)["renovacao_automatica"]


def test_pagamento_sem_periodo_valido_aguarda_reenvio(flow):
    period = flow["transaction"].pop("billing_period")
    assert send(flow).status_code == 503
    assert not user(flow)["assinatura"]
    flow["transaction"]["billing_period"] = period
    assert send(flow).status_code == 200


def test_nova_assinatura_com_checkout_autenticado_pode_substituir_vinculo_antigo(flow):
    flow["db"]["tbl_usuarios"].docs[0].update(
        paddle_subscription_id="sub_" + "z" * 26, paddle_customer_id="ctm_" + "z" * 26,
        assinatura=True, premium_ate=iso(datetime.now(timezone.utc) + timedelta(days=1)))
    assert send(flow).status_code == 200
    assert user(flow)["paddle_subscription_id"] == SUB
    assert user(flow)["pagamento"]["cliente_id"] == CUSTOMER


def test_envelope_nao_pode_forcar_periodo_ou_plano(flow):
    response = send(flow, data={"id": TXN, "assinatura": True, "billing_period": {"ends_at": "2099-01-01T00:00:00Z"}})
    assert response.status_code == 200
    assert user(flow)["premium_ate"] == flow["transaction"]["billing_period"]["ends_at"]


def test_cancelamento_sem_login(flow):
    assert flow["client"].post("/usuarios/me/assinatura/cancelamento", json={}).status_code == 401
    assert not flow["calls"]


def test_pagamento_agrupado_sem_campos_duplicados_e_api_sem_cliente_interno(flow):
    assert send(flow).status_code == 200
    stored = flow["db"]["tbl_usuarios"].docs[0]
    assert not set(CAMPOS_LEGADOS).intersection(stored)
    assert set(stored["pagamento"]) == {
        "assinatura_id", "cliente_id", "status", "premium_ate",
        "cancelamento_agendado", "proxima_renovacao",
    }
    response = flow["client"].get("/me", headers=flow["headers"])
    assert response.json["assinatura"] is True
    assert response.json["renovacao_automatica"] is True
    assert "pagamento" not in response.json
    assert CUSTOMER not in response.get_data(as_text=True)
    assert not any(key.startswith("_paddle_") for key in stored)


def test_pagamento_novo_tem_prioridade_sobre_campos_antigos_e_bloqueia_chat(flow):
    expired = iso(datetime.now(timezone.utc) - timedelta(seconds=1))
    flow["db"]["tbl_usuarios"].docs[0].update(
        pagamento={"status": "active", "premium_ate": expired},
        assinatura=True, premium_ate="2099-01-01T00:00:00Z")
    assert not assinatura_premium_ativa(user(flow))
    assert flow["client"].get("/me", headers=flow["headers"]).json["assinatura"] is False
    assert flow["client"].get("/chats/ana/empresa", headers=flow["headers"]).status_code == 403


def test_migra_legado_preservando_validade_e_cancelamento(flow):
    from migrar_pagamentos import migrar_usuario
    usuarios = flow["db"]["tbl_usuarios"]
    end = flow["transaction"]["billing_period"]["ends_at"]
    usuarios.docs[0].update(assinatura=True, premium_ate=end, paddle_subscription_id=SUB,
        paddle_customer_id=CUSTOMER, paddle_status="active", cancelamento_agendado=True,
        renovacao_automatica=False, falha_pagamento=False)
    assert migrar_usuario(usuarios, "ana") is True
    assert migrar_usuario(usuarios, "ana") is False
    assert not set(CAMPOS_LEGADOS).intersection(usuarios.docs[0])
    assert user(flow)["premium_ate"] == end
    assert user(flow)["assinatura"] is True
    assert user(flow)["renovacao_automatica"] is False
    assert cancel(flow).status_code == 200


def test_migracao_preserva_ultimo_dia_legado_e_nao_reanima_timestamp_expirado():
    from migrar_pagamentos import converter_pagamento
    legacy = {"assinatura": True, "expiracao_assinatura": "28-02-2030"}
    payment = converter_pagamento(legacy)
    assert payment["premium_ate"] == "2030-03-01T02:59:59.999999Z"
    assert premium_ativo({"pagamento": payment}, datetime(2030, 3, 1, 2, 59, 59, tzinfo=timezone.utc))
    assert not premium_ativo({"pagamento": payment}, datetime(2030, 3, 1, 3, tzinfo=timezone.utc))
    legacy["premium_ate"] = "2000-01-01T00:00:00Z"
    assert converter_pagamento(legacy)["premium_ate"] == legacy["premium_ate"]


def test_migracao_pausa_preserva_periodo_pago_para_retomada(flow):
    from migrar_pagamentos import migrar_usuario
    usuarios = flow["db"]["tbl_usuarios"]
    end = flow["transaction"]["billing_period"]["ends_at"]
    usuarios.docs[0].update(assinatura=False, premium_ate=end, paddle_subscription_id=SUB,
        paddle_customer_id=CUSTOMER, paddle_status="paused")
    migrar_usuario(usuarios, "ana")
    assert user(flow)["premium_ate"] == end
    assert not assinatura_premium_ativa(user(flow))
    assert send(flow, "subscription.resumed").status_code == 200
    assert assinatura_premium_ativa(user(flow))


@pytest.mark.parametrize("campo", ["pagamento", "assinatura", "premium_ate", "paddle_status", "_paddle_lock_token"])
def test_cadastro_nao_pode_injetar_dados_de_assinatura(flow, campo):
    response = flow["client"].post("/usuarios", json={campo: {"status": "active"}})
    assert response.status_code == 400
    assert "definidos pelo servidor" in response.json["erro"]
