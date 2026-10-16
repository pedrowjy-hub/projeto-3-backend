import pytest
from unittest.mock import MagicMock, patch
from api import app
import hashlib

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
            "senha":"fhkjfjaskldasbf"
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
            "senha":"fhkjfjaskldasbf"
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
            "senha"
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
        "senha": "fhkjfjaskldasbf",
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

@patch("api.conectar_db")
def test_login_ok(mock_conectar_db):
    senha = "senha_correta"
    senha_hash = hashlib.sha256(senha.encode("utf-8")).hexdigest()

    colecao = mock_conectar_db.return_value.__getitem__.return_value
    colecao.find_one.return_value = {
        "username": "pedrow2",
        "email": "pedro@email.com",
        "senha": senha_hash
    }

    response = app.test_client().post(
        "/login",
        json={"identificador": "pedrow2", "senha": senha}
    )

    assert response.status_code == 200
    assert response.json == {"mensagem":"Login realizado com sucesso"}
    colecao.find_one.assert_called_once_with({
        "$or": [
            {"username": "pedrow2"},
            {"email": "pedrow2"}
        ]
    })

@patch("api.conectar_db")
def test_login_ok_com_email(mock_conectar_db):
    senha = "senha_correta"
    senha_hash = hashlib.sha256(senha.encode("utf-8")).hexdigest()

    colecao = mock_conectar_db.return_value.__getitem__.return_value
    colecao.find_one.return_value = {
        "username": "pedro",
        "email": "pedro@email.com",
        "senha": senha_hash,
    }

    response = app.test_client().post(
        "/login",
        json={
            "identificador": "pedro@email.com",
            "senha": senha,
        },
    )

    assert response.status_code == 200
    assert response.json == {"mensagem": "Login realizado com sucesso"}
    colecao.find_one.assert_called_once_with({
        "$or": [
            {"username": "pedro@email.com"},
            {"email": "pedro@email.com"},
        ]
    })

@patch("api.conectar_db")
def test_login_senha_incorreta(mock_conectar_db):
    colecao = mock_conectar_db.return_value.__getitem__.return_value
    colecao.find_one.return_value = {
        "username": "pedro",
        "email": "pedro@email.com",
        "senha": hashlib.sha256("senha_correta".encode("utf-8")).hexdigest(),
    }

    response = app.test_client().post(
        "/login",
        json={"identificador": "pedro", "senha": "senha_errada"},
    )

    assert response.status_code == 401
    assert response.json == {"erro": "Credenciais inválidas"}
    colecao.find_one.assert_called_once_with({
        "$or": [
            {"username": "pedro"},
            {"email": "pedro"},
        ]
    })

@patch("api.conectar_db")
def test_login_usuario_ou_email_inexistente(mock_conectar_db):
    colecao = mock_conectar_db.return_value.__getitem__.return_value
    colecao.find_one.return_value = None

    response = app.test_client().post(
        "/login",
        json={
            "identificador": "naoexiste@email.com",
            "senha": "qualquer_senha",
        },
    )

    assert response.status_code == 401
    assert response.json == {"erro": "Credenciais inválidas"}
    colecao.find_one.assert_called_once_with({
        "$or": [
            {"username": "naoexiste@email.com"},
            {"email": "naoexiste@email.com"},
        ]
    })

def test_login_sem_identificador(client):
    response = client.post("/login", json={"senha": "senha_correta"})

    assert response.status_code == 400
    assert response.json == {
        "erro": "Identificador e senha são obrigatórios"
    }

def test_login_sem_senha(client):
    response = client.post("/login", json={"identificador": "pedro"})

    assert response.status_code == 400
    assert response.json == {
        "erro": "Identificador e senha são obrigatórios"
    }

def test_login_senha_vazia(client):
    response = client.post(
        "/login",
        json={"identificador": "pedrow2", "senha": ""},
    )

    assert response.status_code == 400
    assert response.json == {
        "erro": "Identificador e senha são obrigatórios"
    }

def test_login_corpo_vazio(client):
    response = client.post("/login", json={})

    assert response.status_code == 400
    assert response.json == {
        "erro": "Identificador e senha são obrigatórios"
    }

def test_login_sem_json(client):
    response = client.post("/login")

    assert response.status_code == 400
    assert response.json == {
        "erro": "Identificador e senha são obrigatórios"
    }

def test_login_identificador_vazio(client):
    response = client.post(
        "/login",
        json={"identificador": "   ", "senha": "senha_correta"},
    )

    assert response.status_code == 400
    assert response.json == {
        "erro": "Identificador e senha são obrigatórios"
    }

def test_login_metodo_nao_permitido(client):
    response = client.get("/login")

    assert response.status_code == 405
