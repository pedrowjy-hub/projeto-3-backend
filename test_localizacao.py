from collections import defaultdict
from unittest.mock import MagicMock, patch
import pytest
from flask_jwt_extended import create_access_token
from api import app, CAMPOS_PUBLICOS


@pytest.fixture
def context():
    with patch.dict(app.config, TESTING=True, JWT_SECRET_KEY="localizacao-chave-de-teste-123456789"), patch("api.conectar_db", return_value=defaultdict(MagicMock)) as conn:
        db = conn.return_value
        db["tbl_usuarios"].find_one.return_value = {"username": "ana", "papel": "usuario", "ativo": True}
        with app.app_context():
            headers = {"Authorization": "Bearer " + create_access_token(identity="ana")}
        yield app.test_client(), db, headers


@pytest.mark.parametrize("body,expected", [({"cidade": " São Paulo ", "estado": " SP "}, {"cidade": "São Paulo", "estado": "SP"}), ({"cidade": "Recife"}, {"cidade": "Recife"}), ({"estado": "Bahia"}, {"estado": "Bahia"}), ({"cidade": "", "estado": "  "}, {"cidade": "", "estado": ""})])
def test_salvar_e_limpar(context, body, expected):
    client, db, headers = context
    assert client.patch("/usuarios/ana", json=body, headers=headers).status_code == 200
    db["tbl_usuarios"].update_one.assert_called_once_with({"username": "ana"}, {"$set": expected})


@pytest.mark.parametrize("value", [None, 123, True, [], {}, "x" * 101])
@pytest.mark.parametrize("field", ["cidade", "estado"])
def test_valores_invalidos(context, field, value):
    client, db, headers = context
    assert client.patch("/usuarios/ana", json={field: value}, headers=headers).status_code == 422
    db["tbl_usuarios"].update_one.assert_not_called()


def test_nao_edita_outro_perfil(context):
    client, db, headers = context
    assert client.patch("/usuarios/outro", json={"cidade": "Recife"}, headers=headers).status_code == 403
    db["tbl_usuarios"].update_one.assert_not_called()


def test_campos_disponiveis_nas_consultas(context):
    client, db, headers = context
    assert CAMPOS_PUBLICOS["cidade"] == CAMPOS_PUBLICOS["estado"] == 1
    db["tbl_usuarios"].find.return_value = []
    for url in ("/usuarios/ana", "/me"):
        assert client.get(url, headers=headers).status_code == 200
        projection = db["tbl_usuarios"].find_one.call_args.args[1]
        assert projection["cidade"] == projection["estado"] == 1
    assert client.get("/usuarios").status_code == 200
    assert db["tbl_usuarios"].find.call_args.args[1]["cidade"] == 1
