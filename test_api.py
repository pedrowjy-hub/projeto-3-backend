import pytest
from unittest.mock import MagicMock, patch
from api import app
from bson import ObjectId

@pytest.fixture
def client():
    app.config["TESTING"] = True 
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
        
@patch("api.conectar_db")
def test_listar_dados_ok(mock_conectar_db):

    mock_db = mock_conectar_db.return_value
    mock_collection = mock_db.__getitem__.return_value

    usuarios = [
        {"username":"pedrow2","nome":"Pedro","email":"email@email.com","contato":"(11)99999-9999","tipo":"influenciador","nicho":"moda","plataformas":["Tiktok","Instagram"],"assinatura":True,"expiracao_assinatura":"23-10-2026"},
        {"username":"teste","nome":"teste","email":"teste@email.com","contato":"(11)99999-9999","tipo":"admin","nicho":"moda","plataformas":["Tiktok"],"assinatura":False,"expiracao_assinatura":"-"}
    ]

    mock_collection.find.return_value = usuarios

    response = app.test_client().get("/usuarios")

    assert response.status_code == 200
    assert response.get_json() == usuarios
    mock_collection.find.assert_called_once_with({},{"_id":0})

@patch("api.conectar_db")
def test_listar_sem_dados(mock_conectar_db):
    colecao = mock_conectar_db.return_value.__getitem__.return_value
    colecao.find.return_value = []

    response = app.test_client().get("/usuarios")

    assert response.status_code == 200
    assert response.get_json() == []
    colecao.find.assert_called_once_with({}, {"_id": 0})

@pytest.mark.parametrize(
    "novo_user",
    [
        {
            "username": "pedrow2",
            "nome": "Pedro",
            "email": "pedro@email.com",
            "contato": "(11)99999-9999",
            "tipo": "influenciador",
            "nicho": "moda",
            "plataformas": ["TikTok", "Instagram"],
            "assinatura": True,
            "expiracao_assinatura": "23-10-2026",
        },
        {
            "username": "moda_urbana",
            "nome": "Moda Urbana",
            "email": "contato@modaurbana.com",
            "contato": "(11)98888-8888",
            "tipo": "empresa",
            "nicho": "moda",
            "plataformas": ["Instagram", "TikTok"],
            "assinatura": False,
            "expiracao_assinatura": "-",
        },
    ],
    ids=["influenciador", "empresa"],
)
@patch("api.conectar_db")
def test_criar_user_ok(mock_conectar_db, novo_user):
    colecao = mock_conectar_db.return_value.__getitem__.return_value

    response = app.test_client().post("/usuarios", json=novo_user)

    assert response.status_code == 201
    assert response.get_json() == {
        "mensagem": "Usuário criado com sucesso",
        "usuario": novo_user["username"],
    }
    colecao.insert_one.assert_called_once_with(novo_user)

def test_criar_user_campos_obrigatorios_ausentes():

    payload = {"username": "pedrow2", "nome": "Pedro"}
    response = app.test_client().post(
        "/usuarios",
        json=payload,
    )

    campos_obrigatorios = [
            "email",
            "contato",
            "tipo",
            "nicho",
            "plataformas",
            "assinatura",
            "expiracao_assinatura",
        ]
    
    campos_faltantes = [
        campo_faltante for campo_faltante in campos_obrigatorios 
        if campo_faltante not in payload
    ]
    
    assert response.status_code == 400
    assert response.get_json() == {
        "erro": "Campos obrigatórios ausentes",
        "campos_faltantes": campos_faltantes
    }

@patch("api.conectar_db")
def test_criar_user_username_ja_cadastrado(mock_conectar_db):
    colecao = mock_conectar_db.return_value.__getitem__.return_value
    colecao.find_one.return_value = {"username": "pedrow2"}
    novo_user = {
        "username": "pedrow2",
        "nome": "Pedro",
        "email": "pedro@email.com",
        "contato": "(11)99999-9999",
        "tipo": "influenciador",
        "nicho": "moda",
        "plataformas": ["TikTok"],
        "assinatura": True,
        "expiracao_assinatura": "23-10-2026",
    }

    response = app.test_client().post("/usuarios", json=novo_user)

    assert response.status_code == 409
    assert response.get_json() == {"erro": "Conflito: username já cadastrado"}
    colecao.find_one.assert_called_once_with({"username": "pedrow2"})
    colecao.insert_one.assert_not_called()



def test_editar_user_sucesso():
    with patch("api.conectar_db") as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.find_one.side_effect = [
            {"username": "rafacelduds"},
            None,
            None,
        ]
        colecao.update_one.return_value.matched_count = 1
        dados = {
            "nome": "Rafael",
            "email": "rafael@example.com",
            "contato": "11999999999",
            "tipo": "influenciador",
            "nicho": "moda",
            "plataformas": ["Instagram", "TikTok"],
        }

        response = app.test_client().put("/usuarios/rafacelduds", json=dados)

        assert response.status_code == 200
        assert response.json == {"mensagem": "Usuário atualizado com sucesso"}
        colecao.update_one.assert_called_once_with(
            {"username": "rafacelduds"},
            {"$set": dados},
        )


def test_editar_user_404():
    with patch("api.conectar_db") as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.find_one.return_value = None
        dados = {
            "nome": "Rafael",
            "email": "rafael@example.com",
            "contato": "11999999999",
            "tipo": "influenciador",
            "nicho": "moda",
            "plataformas": ["Instagram"],
        }

        response = app.test_client().put("/usuarios/fulano", json=dados)

        assert response.status_code == 404
        assert response.json == {"erro": "Usuário não encontrado"}
        colecao.update_one.assert_not_called()


def test_editar_user_400_campos_faltantes():
    dados = {"nome": "Rafael", "email": "rafael@example.com"}

    response = app.test_client().put("/usuarios/rafacelduds", json=dados)

    assert response.status_code == 400
    assert response.json == {
        "erro": "Campos obrigatórios ausentes",
        "campos_faltantes": ["contato", "tipo", "nicho", "plataformas"],
    }


def test_editar_user_409_email_em_uso():
    with patch("api.conectar_db") as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.find_one.side_effect = [
            {"username": "rafacelduds"},
            {"username": "outro_usuario"},
        ]
        dados = {
            "nome": "Rafael",
            "email": "usado@example.com",
            "contato": "11999999999",
            "tipo": "influenciador",
            "nicho": "moda",
            "plataformas": ["Instagram"],
        }

        response = app.test_client().put("/usuarios/rafacelduds", json=dados)

        assert response.status_code == 409
        assert response.json == {"erro": "Conflito: email já cadastrado"}
        colecao.update_one.assert_not_called()


def test_editar_user_422_plataformas_invalidas():
    dados = {
        "nome": "Rafael",
        "email": "rafael@example.com",
        "contato": "11999999999",
        "tipo": "influenciador",
        "nicho": "moda",
        "plataformas": "Instagram",
    }

    response = app.test_client().put("/usuarios/rafacelduds", json=dados)

    assert response.status_code == 422
    assert response.json == {"erro": "Valor inválido para plataformas"}
