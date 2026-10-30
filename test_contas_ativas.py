from collections import defaultdict
from unittest.mock import MagicMock, patch

import pytest
from flask_jwt_extended import create_access_token
from api import app


@pytest.fixture
def context():
    users = {
        "admin": {"username": "admin", "papel": "admin", "ativo": True, "senha": "hash"},
        "ana": {"username": "ana", "papel": "usuario", "tipo_perfil": "empresa", "ativo": True, "senha": "hash"},
        "inativa": {"username": "inativa", "papel": "usuario", "tipo_perfil": "empresa", "ativo": False, "senha": "hash"},
    }
    with patch.dict(app.config, TESTING=True, JWT_SECRET_KEY="teste-contas-ativas-chave-isolada-12345"), patch("api.conectar_db", return_value=defaultdict(MagicMock)) as conn, patch("api.bcrypt.check_password_hash", side_effect=lambda hashed, password: password == "correta"):
        db = conn.return_value
        def find(query, *args):
            if "$or" in query:
                return users.get(query["$or"][0]["username"])
            return users.get(query.get("username"))
        db["tbl_usuarios"].find_one.side_effect = find
        def headers(username):
            with app.app_context():
                return {"Authorization": "Bearer " + create_access_token(identity=username)}
        yield app.test_client(), db, users, headers


def test_desativa_sem_apagar(context):
    client, db, _, headers = context
    response = client.delete("/usuarios/ana", json={"senha": "correta"}, headers=headers("admin"))
    assert response.status_code == 204
    db["tbl_usuarios"].delete_one.assert_not_called()
    db["tbl_usuarios"].update_one.assert_called_once_with({"username": "ana"}, {"$set": {"ativo": False}})


def test_status_admin_e_reativacao(context):
    client, db, _, headers = context
    response = client.patch("/admin/usuarios/inativa/status", json={"ativo": True, "senha": "correta"}, headers=headers("admin"))
    assert response.status_code == 200 and response.json["ativo"] is True
    db["tbl_usuarios"].update_one.assert_called_once_with({"username": "inativa"}, {"$set": {"ativo": True}})


@pytest.mark.parametrize("route", ["/admin", "/me", "/denuncias", "/admin/usuarios"])
def test_token_existente_bloqueado_apos_desativar(context, route):
    client, _, users, headers = context
    auth = headers("ana")
    users["ana"]["ativo"] = False
    assert client.get(route, headers=auth).status_code == 401


def test_login_conta_desativada(context):
    client, _, _, _ = context
    response = client.post("/login", json={"identificador": "inativa", "senha": "correta"})
    assert response.status_code == 403
    assert "access_token" not in response.json


@pytest.mark.parametrize("username,body,expected", [
    ("ana", {"ativo": True, "senha": "correta"}, 403),
    ("admin", {"ativo": "false", "senha": "correta"}, 400),
    ("admin", {"ativo": False, "senha": "incorreta"}, 400),
])
def test_alteracao_restrita(context, username, body, expected):
    client, db, _, headers = context
    assert client.patch("/admin/usuarios/ana/status", json=body, headers=headers(username)).status_code == expected
    db["tbl_usuarios"].update_one.assert_not_called()


def test_admin_nao_desativa_a_si_mesmo(context):
    client, db, _, headers = context
    assert client.delete("/usuarios/admin", json={"senha": "correta"}, headers=headers("admin")).status_code == 400
    assert client.patch("/admin/usuarios/admin/status", json={"ativo": False, "senha": "correta"}, headers=headers("admin")).status_code == 400
    db["tbl_usuarios"].update_one.assert_not_called()


def test_listagem_publica_filtra_inativos(context):
    client, db, _, _ = context
    db["tbl_usuarios"].find.return_value = []
    assert client.get("/usuarios").status_code == 200
    assert db["tbl_usuarios"].find.call_args.args[0]["ativo"] == {"$ne": False}
    client.get("/usuarios/ana")
    assert db["tbl_usuarios"].find_one.call_args.args[0]["ativo"] == {"$ne": False}


def test_cadastro_define_ativo_no_servidor(context):
    client, db, _, _ = context
    data = {"nome": "Nova", "username": "nova", "email": "nova@example.com", "contato": "11999999999", "tipo_perfil": "empresa", "nicho": "moda", "plataformas": ["Instagram"], "senha": "senha-teste", "ativo": False}
    assert client.post("/usuarios", json=data).status_code == 201
    assert db["tbl_usuarios"].insert_one.call_args.args[0]["ativo"] is True
