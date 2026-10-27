from io import BytesIO
from collections import defaultdict
from unittest.mock import MagicMock, patch

import pytest
from bson import BSON, ObjectId
from flask_jwt_extended import create_access_token

from api import app, TAMANHO_MAXIMO_MIDIA_POSTAGEM


@pytest.fixture
def auth(monkeypatch):
    monkeypatch.setitem(app.config, "JWT_SECRET_KEY", "chave-isolada-para-testes-postagens-123456789")
    with app.app_context():
        return {"Authorization": f"Bearer {create_access_token(identity='teste')}"}


def arquivos(quantidade, tamanho=10):
    return [(BytesIO(b"a" * tamanho), f"arquivo-{i}.png", "image/png") for i in range(quantidade)]


@pytest.mark.parametrize("quantidade", [1, 2, 3])
def test_postagem_aceita_arquivos_de_8mb(quantidade, auth):
    with patch("api.conectar_db", return_value=defaultdict(MagicMock)) as conn:
        db = conn.return_value
        db["tbl_usuarios"].find_one.return_value = {"_id": ObjectId(), "papel": "usuario"}
        db["tbl_postagens"].insert_one.side_effect = lambda item: type("Result", (), {"inserted_id": item["_id"]})()
        response = app.test_client().post("/postagens", headers=auth, data={
            "titulo": "Título", "descricao": "Descrição",
            "midia": arquivos(quantidade, TAMANHO_MAXIMO_MIDIA_POSTAGEM),
        })
        assert response.status_code == 201
        post = response.json["postagem"]
        assert len(post["midias"]) == quantidade
        assert all("dados" not in item for item in post["midias"])
        stored = db["tbl_postagens"].insert_one.call_args.args[0]
        assert len(BSON.encode(stored)) < 16 * 1024 * 1024
        if quantidade > 1:
            extras = db["tbl_midias_postagens"].insert_many.call_args.args[0]
            assert len(extras) == quantidade - 1
            assert all(len(BSON.encode(item)) < 16 * 1024 * 1024 for item in extras)
            for index, extra in enumerate(extras, 1):
                assert post["midias"][index]["id"] == str(extra["_id"])
                db["tbl_postagens"].find_one.return_value = stored
                db["tbl_midias_postagens"].find_one.return_value = extra
                media = app.test_client().get(f"/postagens/{stored['_id']}/midia/{index}")
                assert media.status_code == 200
                assert len(media.data) == TAMANHO_MAXIMO_MIDIA_POSTAGEM


@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize("count,size,status", [(4, 10, 400), (2, 0, 422), (2, TAMANHO_MAXIMO_MIDIA_POSTAGEM + 1, 413)])
def test_limites_multiplos_arquivos(method, count, size, status, auth):
    with patch("api.conectar_db", return_value=defaultdict(MagicMock)) as conn:
        url = "/postagens" if method == "post" else f"/postagens/{ObjectId()}"
        response = getattr(app.test_client(), method)(url, headers=auth, data={
            "titulo": "Título", "descricao": "Descrição", "midia": arquivos(count, size),
        })
        assert response.status_code == status
        conn.return_value["tbl_postagens"].insert_one.assert_not_called()
        conn.return_value["tbl_postagens"].update_one.assert_not_called()


def test_editar_substitui_anexos_e_exclui_extras_antigos(auth):
    post_id, owner_id, old_id = ObjectId(), ObjectId(), ObjectId()
    with patch("api.conectar_db", return_value=defaultdict(MagicMock)) as conn:
        db = conn.return_value
        db["tbl_usuarios"].find_one.return_value = {"_id": owner_id, "papel": "usuario"}
        db["tbl_postagens"].find_one.return_value = {
            "_id": post_id, "usuario_id": owner_id, "midias": [{"nome": "antigo"}, {"id": str(old_id)}],
        }
        response = app.test_client().patch(f"/postagens/{post_id}", headers=auth, data={"midia": arquivos(3)})
        assert response.status_code == 200
        update = db["tbl_postagens"].update_one.call_args.args[1]["$set"]
        assert len(update["midias"]) == 3
        db["tbl_midias_postagens"].delete_many.assert_called_once_with({"_id": {"$in": [old_id]}})


def test_deletar_limpa_arquivos_extras(auth):
    post_id, owner_id, extra_id = ObjectId(), ObjectId(), ObjectId()
    with patch("api.conectar_db", return_value=defaultdict(MagicMock)) as conn:
        db = conn.return_value
        db["tbl_usuarios"].find_one.return_value = {"_id": owner_id, "papel": "usuario"}
        db["tbl_postagens"].find_one.return_value = {"_id": post_id, "usuario_id": owner_id, "midias": [{"id": str(extra_id)}]}
        response = app.test_client().delete(f"/postagens/{post_id}", headers=auth)
        assert response.status_code == 204
        db["tbl_midias_postagens"].delete_many.assert_called_once_with({"_id": {"$in": [extra_id]}})


def test_indice_inexistente_e_postagem_antiga():
    post_id = ObjectId()
    with patch("api.conectar_db", return_value=defaultdict(MagicMock)) as conn:
        conn.return_value["tbl_postagens"].find_one.return_value = {
            "_id": post_id, "midia": {"dados": b"antigo", "nome": "antigo.txt", "tipo": "text/plain"},
        }
        client = app.test_client()
        assert client.get(f"/postagens/{post_id}/midia/0").data == b"antigo"
        assert client.get(f"/postagens/{post_id}/midia/1").status_code == 404
        assert client.get(f"/postagens/{post_id}/midia").data == b"antigo"
