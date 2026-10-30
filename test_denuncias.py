from collections import defaultdict
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId
from flask_jwt_extended import create_access_token
from api import app


@pytest.fixture
def context():
    with patch.dict(app.config, TESTING=True, JWT_SECRET_KEY="chave-apenas-para-testes-denuncias-123456"), patch("api.conectar_db", return_value=defaultdict(MagicMock)) as connection:
        db = connection.return_value
        users = {
            "ana": {"username": "ana", "papel": "usuario", "tipo_perfil": "influenciador"},
            "marca": {"username": "marca", "papel": "usuario", "tipo_perfil": "empresa"},
            "admin": {"username": "admin", "papel": "admin"},
        }
        db["tbl_usuarios"].find_one.side_effect = lambda query, *args: next((u for u in users.values() if all(u.get(k) == v for k, v in query.items())), None)
        def headers(username):
            with app.app_context():
                return {"Authorization": "Bearer " + create_access_token(identity=username)}
        yield app.test_client(), db, headers


def payload():
    return {"denunciado": "marca", "motivo": "Spam", "descricao": "Recebi mensagens repetidas e indesejadas."}


def document():
    return {"_id": ObjectId(), "denunciante": "ana", **payload(), "status": "pendente", "resposta": "", "administrador": None, "criada_em": datetime.now(timezone.utc), "atualizada_em": datetime.now(timezone.utc)}


def test_criacao_e_identidade(context):
    client, db, headers = context
    response = client.post("/denuncias", json=payload(), headers=headers("ana"))
    assert response.status_code == 201
    result = response.json["denuncia"]
    assert result["denunciante"] == "ana" and result["status"] == "pendente"
    assert ObjectId.is_valid(result["id"])
    assert result["criada_em"].endswith("+00:00")
    db["tbl_denuncias"].insert_one.assert_called_once()


@pytest.mark.parametrize("changes,code", [({"denunciante": "marca"}, 400), ({"status": "concluida"}, 400), ({"denunciado": "ana"}, 400), ({"denunciado": "inexistente"}, 404), ({"denunciado": "admin"}, 404), ({"descricao": "curto"}, 400), ({"descricao": "x" * 2001}, 400), ({"motivo": {}}, 400), ({"motivo": "inválido"}, 400)])
def test_criacao_invalida(context, changes, code):
    client, db, headers = context
    assert client.post("/denuncias", json={**payload(), **changes}, headers=headers("ana")).status_code == code
    db["tbl_denuncias"].insert_one.assert_not_called()


def test_lista_privada(context):
    client, db, headers = context
    db["tbl_denuncias"].find.return_value.sort.return_value = [document()]
    response = client.get("/denuncias?denunciante=marca", headers=headers("ana"))
    assert response.status_code == 200
    db["tbl_denuncias"].find.assert_called_once_with({"denunciante": "ana"})


@pytest.mark.parametrize("route,method", [("/denuncias", "get"), ("/denuncias", "post"), ("/admin/denuncias", "get"), ("/admin/denuncias/123", "patch")])
def test_exige_token(context, route, method):
    client, _, _ = context
    assert getattr(client, method)(route).status_code == 401


def test_restricao_admin_e_usuario_removido(context):
    client, db, headers = context
    assert client.get("/admin/denuncias", headers=headers("ana")).status_code == 403
    assert client.patch("/admin/denuncias/123", json={}, headers=headers("ana")).status_code == 403
    assert client.get("/denuncias", headers=headers("removido")).status_code == 401
    assert client.post("/denuncias", json=payload(), headers=headers("admin")).status_code == 403
    db["tbl_denuncias"].update_one.assert_not_called()


def test_listagem_admin(context):
    client, db, headers = context
    db["tbl_denuncias"].find.return_value.sort.return_value = []
    assert client.get("/admin/denuncias?status=pendente", headers=headers("admin")).json == []
    db["tbl_denuncias"].find.assert_called_once_with({"status": "pendente"})
    assert client.get("/admin/denuncias?status=errado", headers=headers("admin")).status_code == 400


@pytest.mark.parametrize("body", [{}, [], {"status": []}, {"status": "errado"}, {"resposta": 1}, {"resposta": "x" * 2001}, {"denunciado": "outro"}, {"status": "concluida", "resposta": "   "}])
def test_analise_invalida(context, body):
    client, db, headers = context
    item = document()
    db["tbl_denuncias"].find_one.return_value = item
    assert client.patch(f'/admin/denuncias/{item["_id"]}', json=body, headers=headers("admin")).status_code == 400
    db["tbl_denuncias"].update_one.assert_not_called()


def test_conclusao_e_concorrencia(context):
    client, db, headers = context
    item = document()
    db["tbl_denuncias"].find_one.return_value = item
    db["tbl_denuncias"].update_one.return_value.matched_count = 1
    response = client.patch(f'/admin/denuncias/{item["_id"]}', json={"status": "concluida", "resposta": "Relato analisado."}, headers=headers("admin"))
    assert response.status_code == 200
    assert response.json["denuncia"]["administrador"] == "admin"
    assert response.json["denuncia"]["status"] == "concluida"
    db["tbl_usuarios"].delete_one.assert_not_called()
    db["tbl_denuncias"].update_one.return_value.matched_count = 0
    assert client.patch(f'/admin/denuncias/{item["_id"]}', json={"status": "em_analise"}, headers=headers("admin")).status_code == 409


def test_denuncia_inexistente(context):
    client, db, headers = context
    db["tbl_denuncias"].find_one.return_value = None
    for id in ("invalido", str(ObjectId())):
        assert client.patch(f"/admin/denuncias/{id}", json={"status": "em_analise"}, headers=headers("admin")).status_code == 404
