from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from flask_jwt_extended import create_access_token

from api import app


@pytest.fixture
def autenticacao(monkeypatch):
    # O bloqueio de contas tem testes próprios; aqui isolamos as regras da análise.
    monkeypatch.setattr(app.extensions["flask-jwt-extended"],
                        "_token_in_blocklist_callback", lambda _header, _payload: False)
    monkeypatch.setitem(app.config, "JWT_SECRET_KEY", "segredo-testes-analises-youtube-1234567890")
    def headers(username):
        with app.app_context():
            token = create_access_token(identity=username)
        return {"Authorization": f"Bearer {token}"}
    return headers


@pytest.mark.parametrize("identidade,tipo", [("marca", "empresa"), ("duds", "influenciador")])
def test_consulta_historico_do_influenciador(autenticacao, identidade, tipo):
    with patch("api.conectar_db") as conectar:
        db = conectar.return_value
        usuarios = db["tbl_usuarios"]
        usuarios.find_one.side_effect = [{"tipo_perfil": tipo}, {"tipo_perfil": "influenciador"}]
        analises = db["tbl_analises_influenciadores"]
        analises.find.return_value.sort.return_value.limit.return_value = [
            {"data_referencia": "2026-10-30", "coletado_em": datetime(2026, 10, 30, tzinfo=timezone.utc)},
            {"data_referencia": "2026-10-29"},
        ]
        response = app.test_client().get("/analises/youtube/duds", headers=autenticacao(identidade))
        assert response.status_code == 200
        assert [item["data_referencia"] for item in response.json["historico"]] == ["2026-10-29", "2026-10-30"]
        assert response.json["historico"][1]["coletado_em"] == "2026-10-30T00:00:00+00:00"
        analises.find.assert_called_once_with({"username": "duds", "plataforma": "youtube"}, {"_id": 0, "username": 0})
        analises.find.return_value.sort.assert_called_once_with("data_referencia", -1)
        analises.find.return_value.sort.return_value.limit.assert_called_once_with(90)


@pytest.mark.parametrize("solicitante,alvo,status", [
    (None, None, 401),
    ({"tipo_perfil": "influenciador"}, {"tipo_perfil": "influenciador"}, 403),
    ({"tipo_perfil": "desconhecido"}, None, 403),
    ({"tipo_perfil": "empresa"}, None, 404),
    ({"tipo_perfil": "empresa"}, {"tipo_perfil": "empresa"}, 404),
])
def test_permissoes_e_perfil_alvo(autenticacao, solicitante, alvo, status):
    with patch("api.conectar_db") as conectar:
        db = conectar.return_value
        db["tbl_usuarios"].find_one.side_effect = [solicitante, alvo]
        response = app.test_client().get("/analises/youtube/duds", headers=autenticacao("marca"))
        assert response.status_code == status
        db["tbl_analises_influenciadores"].find.assert_not_called()


@pytest.mark.parametrize("rota", ["/analises/youtube", "/analises/youtube/duds"])
def test_exige_token(rota):
    with patch("api.conectar_db") as conectar:
        assert app.test_client().get(rota).status_code == 401
        conectar.assert_not_called()


@pytest.mark.parametrize("rota", ["/analises/youtube", "/analises/youtube/duds"])
def test_historico_vazio(autenticacao, rota):
    with patch("api.conectar_db") as conectar:
        db = conectar.return_value
        db["tbl_usuarios"].find_one.return_value = {"tipo_perfil": "influenciador"}
        analises = db["tbl_analises_influenciadores"]
        analises.find.return_value.sort.return_value.limit.return_value = []
        response = app.test_client().get(rota, headers=autenticacao("duds"))
        assert response.status_code == 200
        assert response.json == {"plataforma": "youtube", "historico": []}
        assert analises.find.call_args.args[0]["username"] == "duds"
