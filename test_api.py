import pytest
from unittest.mock import MagicMock, patch
from api import app

@pytest.fixture
def client():
    app.config['TESTING'] = True 
    with app.test_client() as test_client:
        yield test_client

def test_buscar_user():
    with patch('api.conectar_db') as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.find_one.return_value = {"username": "rafacelduds", "email": "rafaelfd5@al.insper.edu.br"}

        response = app.test_client().get("/usuarios/rafacelduds")

        assert response.status_code == 200
        assert response.json == {"username": "rafacelduds", "email": "rafaelfd5@al.insper.edu.br"}
        colecao.find_one.assert_called_once_with(
            {"username": "rafacelduds"},
            {"_id": 0},
        )
        
def test_buscar_user_404():
    with patch('api.conectar_db') as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.find_one.return_value = None

        response = app.test_client().get("/usuarios/fulano")

        assert response.status_code == 404
        colecao.find_one.assert_called_once()

def test_deletar_user():
    with patch('api.conectar_db') as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.delete_one.return_value.deleted_count = 1

        response = app.test_client().delete("/usuarios/rafacelduds")
        assert response.status_code == 204
        colecao.delete_one.assert_called_once_with(
            {"username": "rafacelduds"}
        )

def test_deletar_user_404():
    with patch('api.conectar_db') as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.delete_one.return_value.deleted_count = 0

        response = app.test_client().delete("/usuarios/fulano")

        assert response.status_code == 404
        assert response.json == {"erro": "Usuário não encontrado"}