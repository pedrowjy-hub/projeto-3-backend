import pytest
from unittest.mock import MagicMock, patch
from api import app
from bson import ObjectId

@pytest.fixture
def client():
    app.config["TESTING"] = True 
    with app.test_client() as test_client:
        yield test_client

@patch("api.conectar_db")
def test_listar_dados_ok(mock_conectar_db):

    mock_db = mock_conectar_db
    mock_collection = mock_db.__getitem__.return_value

    usuarios = [
        {"username":"pedrow2","nome":"Pedro","email":"email@email.com","contato":"(11)99999-9999","tipo":"influenciador","nicho":"moda","plataformas":["Tiktok","Instagram"],"assinatura":True,"expiracao_assinatura":"23-10-2026"},
        {"username":"teste","nome":"teste","email":"teste@email.com","contato":"(11)99999-9999","tipo":"admin","nicho":"moda","plataformas":["Tiktok"],"assinatura":False,"expiracao_assinatura":"-"}
    ]

    mock_collection.find.return_value = usuarios

    response = app.client().get("/usuarios")

    assert response.status_code == 200
    assert response.get_json() == usuarios
    mock_collection.find.assert_called_once_with({},{"_id":0})

