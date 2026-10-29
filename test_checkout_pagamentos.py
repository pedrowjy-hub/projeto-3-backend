"""Exercita o endpoint real contra um servidor HTTP local que simula o Paddle."""
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch

import pytest
from flask_jwt_extended import create_access_token
from pymongo.errors import DuplicateKeyError

import pagamentos
from api import app

PRICE_ID = "pri_" + "a" * 26
TRANSACTION_ID = "txn_" + "b" * 26
PRICE = {"id": PRICE_ID, "status": "active", "unit_price": {"amount": "2990", "currency_code": "BRL"},
         "billing_cycle": {"interval": "month", "frequency": 1}, "trial_period": None}


class Checkouts:
    def __init__(self):
        self.document = None

    def find_one(self, query):
        return copy.deepcopy(self.document)

    def insert_one(self, document):
        if self.document:
            raise DuplicateKeyError("checkout duplicado")
        self.document = copy.deepcopy(document)

    def update_one(self, query, change):
        if self.document and all(self.document.get(key) == value for key, value in query.items()):
            self.document.update(change["$set"])

    def delete_one(self, query):
        if self.document and all(self.document.get(key) == value for key, value in query.items()):
            self.document = None


@pytest.fixture
def scenario(monkeypatch):
    monkeypatch.setenv("PADDLE_API_KEY", "pdl_sdbx_apikey_apenas_teste_local")
    monkeypatch.setenv("PADDLE_CLIENT_TOKEN", "test_apenas_teste_local")
    monkeypatch.setenv("PADDLE_PREMIUM_PRICE_ID", PRICE_ID)
    monkeypatch.setitem(app.config, "JWT_SECRET_KEY", "segredo-exclusivo-checkout-testes-1234567890")
    state = {"calls": [], "price": copy.deepcopy(PRICE), "transaction": None,
             "post_status": 201, "user": {"username": "usuario_teste", "papel": "usuario",
                                         "tipo_perfil": "empresa", "assinatura": False}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            state["calls"].append(("GET", self.path, None))
            data = state["price"] if self.path == f"/prices/{PRICE_ID}" else state["transaction"]
            self.answer(200, {"data": data})

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["calls"].append(("POST", self.path, payload))
            assert self.path == "/transactions"
            if state["post_status"] != 201:
                self.answer(state["post_status"], {"error": {"code": state.get("error_code", "internal_error")}})
                return
            state["transaction"] = {"id": TRANSACTION_ID, "status": "draft",
                "custom_data": payload["custom_data"], "collection_mode": payload["collection_mode"],
                "currency_code": payload["currency_code"], "items": [{"quantity": 1, "price": {"id": PRICE_ID}}]}
            self.answer(201, {"data": state["transaction"]})

        def answer(self, status, data):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Somente o teste substitui a URL; a aplicação não aceita URL por ambiente ou pelo cliente.
    monkeypatch.setattr(pagamentos, "PADDLE_API_BASE", f"http://127.0.0.1:{server.server_port}")
    users, checkouts = MagicMock(), Checkouts()
    users.find_one.side_effect = lambda *_args, **_kwargs: copy.deepcopy(state["user"])
    with app.app_context():
        token = create_access_token(identity="usuario_teste")
    with patch("api.conectar_db") as db:
        db.return_value.__getitem__.side_effect = {"tbl_usuarios": users, "tbl_checkout_pagamentos": checkouts}.__getitem__
        state.update(client=app.test_client(), headers={"Authorization": f"Bearer {token}"}, users=users, checkouts=checkouts)
        yield state
    server.shutdown()
    server.server_close()
    thread.join()


URL = "/usuarios/me/assinatura/checkout"


def post(scenario, data=None):
    return scenario["client"].post(URL, json={} if data is None else data, headers=scenario["headers"])


@pytest.mark.parametrize("profile", ["empresa", "influenciador"])
def test_checkout_real_http_cria_transacao_sem_liberar_premium(scenario, profile):
    scenario["user"]["tipo_perfil"] = profile
    response = post(scenario)
    assert response.status_code == 201
    assert response.json == {"environment": "sandbox", "transaction_id": TRANSACTION_ID,
                             "client_token": "test_apenas_teste_local", "reused": False}
    sent = scenario["calls"][-1][2]
    assert sent["custom_data"]["username"] == "usuario_teste"
    assert sent["items"] == [{"price_id": PRICE_ID, "quantity": 1}]
    assert "PADDLE_API_KEY" not in response.get_data(as_text=True)
    scenario["users"].update_one.assert_not_called()
    assert scenario["checkouts"].document["status"] == "pending"


def test_reutiliza_checkout_pendente(scenario):
    assert post(scenario).status_code == 201
    response = post(scenario)
    assert response.status_code == 200
    assert response.json["reused"] is True
    assert len([call for call in scenario["calls"] if call[0] == "POST"]) == 1


def test_checkout_pago_nao_cria_outra_assinatura_nem_concede_acesso(scenario):
    assert post(scenario).status_code == 201
    scenario["transaction"]["status"] = "completed"
    assert post(scenario).status_code == 409
    assert len([call for call in scenario["calls"] if call[0] == "POST"]) == 1
    scenario["users"].update_one.assert_not_called()


@pytest.mark.parametrize("data", [{"username": "outro"}, {"price_id": "outro"}, {"assinatura": True}, []])
def test_nao_aceita_configuracao_do_cliente(scenario, data):
    assert post(scenario, data).status_code == 400
    assert not scenario["calls"]


def test_sem_login(scenario):
    assert scenario["client"].post(URL).status_code == 401
    assert not scenario["calls"]


@pytest.mark.parametrize("user,status", [(None, 401),
    ({"papel": "admin", "tipo_perfil": "empresa"}, 403),
    ({"papel": "usuario", "tipo_perfil": "outro"}, 403),
    ({"papel": "usuario", "tipo_perfil": "empresa", "assinatura": True, "expiracao_assinatura": "31-12-2099"}, 409)])
def test_valida_conta_e_assinatura(scenario, user, status):
    scenario["user"] = user
    assert post(scenario).status_code == status
    assert not scenario["calls"]


@pytest.mark.parametrize("key,value", [("PADDLE_API_KEY", "pdl_live_apikey_nao_usar"),
    ("PADDLE_CLIENT_TOKEN", "live_nao_usar"), ("PADDLE_PREMIUM_PRICE_ID", "errado")])
def test_rejeita_configuracao_insegura(scenario, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    assert post(scenario).status_code == 503
    assert not scenario["calls"]


@pytest.mark.parametrize("field,value", [("unit_price", {"amount": "1", "currency_code": "BRL"}),
    ("billing_cycle", None), ("status", "archived"), ("trial_period", {"interval": "day", "frequency": 7})])
def test_valida_preco_antes_de_criar_transacao(scenario, field, value):
    scenario["price"][field] = value
    assert post(scenario).status_code == 503
    assert not [call for call in scenario["calls"] if call[0] == "POST"]


def test_falha_incerta_impede_post_duplicado(scenario):
    scenario["post_status"] = 500
    assert post(scenario).status_code == 502
    assert scenario["checkouts"].document["status"] == "needs_review"
    assert post(scenario).status_code == 409
    assert len([call for call in scenario["calls"] if call[0] == "POST"]) == 1


def test_configuracao_default_payment_link_retorna_erro_util(scenario):
    scenario["post_status"] = 400
    scenario["error_code"] = "transaction_default_checkout_url_not_set"
    response = post(scenario)
    assert response.status_code == 502
    assert "Default payment link" in response.json["erro"]
    assert scenario["checkouts"].document is None


@pytest.mark.parametrize("ativa", [True, False])
def test_paddle_configurado_bloqueia_alteracao_manual(scenario, ativa):
    response = scenario["client"].patch("/usuarios/usuario_teste/assinatura",
        json={"assinatura": ativa, "expiracao_assinatura": "31-12-2099"}, headers=scenario["headers"])
    assert response.status_code == 409
    scenario["users"].update_one.assert_not_called()
