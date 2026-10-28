from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from flask_jwt_extended import create_access_token

from api import app, assinatura_premium_ativa


@pytest.mark.parametrize("assinatura,expiracao,esperado", [
    (False, "31-12-2099", False),
    (True, "27-10-2026", False),
    (True, "28-10-2026", True),
    (True, "29-10-2026", True),
    (True, None, False),
    (True, "31-02-2026", False),
    (True, "2026-10-29", False),
    ("true", "29-10-2026", False),
])
def test_validade_premium(assinatura, expiracao, esperado):
    assert assinatura_premium_ativa({
        "assinatura": assinatura, "expiracao_assinatura": expiracao,
    }, hoje=date(2026, 10, 28)) is esperado


CHAT = "/chats/influenciador_1/empresa_1"
OPERACOES = [
    ("POST", "/chats", {"id_influenciador": "influenciador_1", "id_empresa": "empresa_1"}),
    ("GET", CHAT, None),
    ("POST", f"{CHAT}/mensagens", {"mensagem": "Olá!"}),
    ("POST", f"{CHAT}/arquivos", {"nome": "a.pdf", "url": "https://example.com/a.pdf", "tipo": "application/pdf"}),
]


@pytest.fixture(params=["influenciador_1", "empresa_1"])
def token(monkeypatch, request):
    monkeypatch.setitem(app.config, "JWT_SECRET_KEY", "segredo-exclusivo-testes-premium-1234567890")
    with app.app_context():
        return create_access_token(identity=request.param)


@pytest.mark.parametrize("metodo,url,dados", OPERACOES)
@pytest.mark.parametrize("conta", [
    {"assinatura": False},
    {"assinatura": True, "expiracao_assinatura": "01-01-2000"},
    {"assinatura": True},
    {"assinatura": True, "expiracao_assinatura": "data-invalida"},
])
def test_chat_bloqueia_conta_sem_premium_valido(token, metodo, url, dados, conta):
    with patch("api.conectar_db") as conectar:
        usuarios, chats = MagicMock(), MagicMock()
        conectar.return_value.__getitem__.side_effect = {"tbl_usuarios": usuarios, "tbl_chats": chats}.__getitem__
        usuarios.find_one.return_value = {"username": "influenciador_1", **conta}
        response = app.test_client().open(
            url, method=metodo, json=dados,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403
        assert response.json["codigo"] == "premium_necessario"
        chats.find_one.assert_not_called()
        chats.insert_one.assert_not_called()
        chats.update_one.assert_not_called()


@pytest.mark.parametrize("metodo,url,dados", OPERACOES)
def test_chat_exige_login(metodo, url, dados):
    with patch("api.conectar_db") as conectar:
        response = app.test_client().open(url, method=metodo, json=dados)
        assert response.status_code == 401
        conectar.assert_not_called()


def test_mensagem_premium_usa_a_mesma_colecao_do_chat(token):
    with patch("api.conectar_db") as conectar:
        usuarios, chats = MagicMock(), MagicMock()
        conectar.return_value.__getitem__.side_effect = {"tbl_usuarios": usuarios, "tbl_chats": chats}.__getitem__
        usuarios.find_one.return_value = {
            "username": "influenciador_1", "assinatura": True,
            "expiracao_assinatura": "31-12-2099",
        }
        chats.update_one.return_value.matched_count = 1
        response = app.test_client().post(
            f"{CHAT}/mensagens", json={"mensagem": "Olá!", "assinatura": True},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 201
        chats.update_one.assert_called_once()
