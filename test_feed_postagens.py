from collections import defaultdict
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from bson import ObjectId

from api import app


def test_feed_reune_usuarios_e_expoe_apenas_identidade_publica():
    empresa_id, creator_id = ObjectId(), ObjectId()
    autores = [
        {"_id": empresa_id, "nome": "Marca", "username": "marca", "tipo_perfil": "empresa",
         "email": "privado@example.com", "senha": "hash-privado", "contato": "privado"},
        {"_id": creator_id, "nome": "Creator", "username": "creator", "tipo_perfil": "influenciador"},
    ]
    postagens = [
        {"_id": ObjectId(), "usuario_id": autor["_id"], "titulo": titulo,
         "descricao": "Detalhes", "criada_em": datetime.now(timezone.utc),
         "midia": {"nome": "trabalho.png", "tipo": "image/png", "dados": b"privado"}}
        for autor, titulo in zip(autores, ["Proposta de trabalho", "Meu portfolio"])
    ]
    with patch("api.conectar_db", return_value=defaultdict(MagicMock)) as connection:
        db = connection.return_value
        db["tbl_postagens"].find.return_value = postagens
        db["tbl_usuarios"].find.return_value = autores
        response = app.test_client().get("/postagens")

        assert response.status_code == 200
        assert [post["autor"]["username"] for post in response.json] == ["marca", "creator"]
        assert [post["titulo"] for post in response.json] == ["Proposta de trabalho", "Meu portfolio"]
        for post in response.json:
            assert set(post["autor"]) == {"nome", "username", "tipo_perfil"}
            assert "dados" not in post["midia"]
        db["tbl_usuarios"].find.assert_called_once()
        assert set(db["tbl_usuarios"].find.call_args.args[0]["_id"]["$in"]) == {empresa_id, creator_id}


def test_feed_preserva_publicacao_de_perfil_removido():
    with patch("api.conectar_db", return_value=defaultdict(MagicMock)) as connection:
        db = connection.return_value
        db["tbl_postagens"].find.return_value = [
            {"_id": ObjectId(), "usuario_id": ObjectId(), "titulo": "Trabalho anterior"}
        ]
        db["tbl_usuarios"].find.return_value = []
        response = app.test_client().get("/postagens")
        assert response.status_code == 200
        assert response.json[0]["autor"] is None
        assert response.json[0]["titulo"] == "Trabalho anterior"


def test_feed_vazio_nao_consulta_autores():
    with patch("api.conectar_db", return_value=defaultdict(MagicMock)) as connection:
        db = connection.return_value
        db["tbl_postagens"].find.return_value = []
        response = app.test_client().get("/postagens")
        assert response.status_code == 200
        assert response.json == []
        db["tbl_usuarios"].find.assert_not_called()
