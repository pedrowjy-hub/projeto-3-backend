import pytest
from unittest.mock import MagicMock, patch
from datetime import timedelta
from flask_jwt_extended import create_access_token, decode_token
from api import app, bcrypt

PROJECAO_PUBLICA = {
    "_id": 0,
    "username": 1,
    "nome": 1,
    "tipo_perfil": 1,
    "nicho": 1,
    "plataformas": 1,
    "descricao": 1,
}


@pytest.fixture
def token_admin(monkeypatch):
    monkeypatch.setitem(app.config, "JWT_SECRET_KEY", "chave-exclusiva-dos-testes-admin-1234567890")
    with app.app_context():
        return create_access_token(identity="testeadmin")


@pytest.fixture
def token_usuario(monkeypatch):
    monkeypatch.setitem(app.config, "JWT_SECRET_KEY", "chave-exclusiva-dos-testes-admin-1234567890")
    with app.app_context():
        return create_access_token(identity="rafacelduds")


@pytest.fixture
def token_outro_usuario(monkeypatch):
    monkeypatch.setitem(app.config, "JWT_SECRET_KEY", "chave-exclusiva-dos-testes-admin-1234567890")
    with app.app_context():
        return create_access_token(identity="outro_usuario")


@pytest.fixture
def token_influenciador(monkeypatch):
    monkeypatch.setitem(app.config, "JWT_SECRET_KEY", "chave-exclusiva-dos-testes-admin-1234567890")
    with app.app_context():
        return create_access_token(identity="influenciador_1")


def cabecalho_autorizacao(token):
    return {"Authorization": f"Bearer {token}"}


def test_admin_acesso_permitido(client, token_admin):
    with patch("api.conectar_db") as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.find_one.return_value = {
            "username": "testeadmin", "papel": "admin"
        }

        response = client.get(
            "/admin", headers={"Authorization": f"Bearer {token_admin}"}
        )

        assert response.status_code == 200
        assert response.json == {
            "mensagem": "Acesso concedido, bem-vindo chefe"
        }
        conn.return_value.__getitem__.assert_called_once_with("tbl_testes")
        colecao.find_one.assert_called_once_with(
            {"username": "testeadmin"}, {"_id": 0}
        )


@pytest.mark.parametrize("papel", ["usuario", None])
def test_admin_acesso_negado(client, token_admin, papel):
    with patch("api.conectar_db") as conn:
        usuario = {"username": "testeadmin"}
        if papel is not None:
            usuario["papel"] = papel
        conn.return_value.__getitem__.return_value.find_one.return_value = usuario

        response = client.get(
            "/admin", headers={"Authorization": f"Bearer {token_admin}"}
        )

        assert response.status_code == 403
        assert response.json == {"erro": "Acesso negado"}


def test_admin_usuario_inexistente(client, token_admin):
    with patch("api.conectar_db") as conn:
        conn.return_value.__getitem__.return_value.find_one.return_value = None

        response = client.get(
            "/admin", headers={"Authorization": f"Bearer {token_admin}"}
        )

        assert response.status_code == 404
        assert response.json == {"erro": "Usuário não encontrado"}


def test_admin_sem_token(client):
    with patch("api.conectar_db") as conn:
        response = client.get("/admin")

        assert response.status_code == 401
        conn.assert_not_called()


def test_admin_token_invalido(client, token_admin):
    with patch("api.conectar_db") as conn:
        response = client.get(
            "/admin", headers={"Authorization": "Bearer token-invalido"}
        )

        assert response.status_code == 422
        conn.assert_not_called()


def test_admin_token_expirado(client, token_admin):
    with app.app_context():
        token = create_access_token(
            identity="testeadmin", expires_delta=timedelta(minutes=-1)
        )

    with patch("api.conectar_db") as conn:
        response = client.get(
            "/admin", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 401
        conn.assert_not_called()

@pytest.fixture
def client():
    app.config["TESTING"] = True 
    with app.test_client() as test_client:
        yield test_client

def test_buscar_user():
    with patch('api.conectar_db') as conn:
        colecao = conn.return_value.__getitem__.return_value
        usuario_publico = {
            "username": "rafacelduds",
            "nome": "Rafael",
            "tipo_perfil": "influenciador",
            "nicho": "moda",
            "plataformas": ["Instagram"],
            "descricao": "Criador de conteúdo",
        }
        colecao.find_one.return_value = usuario_publico

        response = app.test_client().get("/usuarios/rafacelduds")

        assert response.status_code == 200
        assert response.json == usuario_publico
        colecao.find_one.assert_called_once_with(
            {"username": "rafacelduds", "papel": "usuario"},
            PROJECAO_PUBLICA,
        )
        
def test_buscar_user_404():
    with patch('api.conectar_db') as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.find_one.return_value = None

        response = app.test_client().get("/usuarios/fulano")

        assert response.status_code == 404
        colecao.find_one.assert_called_once()

def test_deletar_user(token_usuario):
    with patch('api.conectar_db') as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.find_one.return_value = {
            "username": "rafacelduds", "papel": "usuario"
        }
        colecao.delete_one.return_value.deleted_count = 1

        response = app.test_client().delete(
            "/usuarios/rafacelduds",
            headers=cabecalho_autorizacao(token_usuario),
        )
        assert response.status_code == 204
        colecao.find_one.assert_called_once_with(
            {"username": "rafacelduds"}, {"_id": 0, "papel": 1}
        )
        colecao.delete_one.assert_called_once_with(
            {"username": "rafacelduds"}
        )

def test_deletar_user_404(token_admin):
    with patch('api.conectar_db') as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.find_one.return_value = {
            "username": "testeadmin", "papel": "admin"
        }
        colecao.delete_one.return_value.deleted_count = 0

        response = app.test_client().delete(
            "/usuarios/fulano",
            headers=cabecalho_autorizacao(token_admin),
        )

        assert response.status_code == 404
        assert response.json == {"erro": "Usuário não encontrado"}


def test_deletar_user_sem_token():
    with patch('api.conectar_db') as conn:
        response = app.test_client().delete("/usuarios/rafacelduds")

        assert response.status_code == 401
        conn.assert_not_called()


def test_deletar_user_de_outro_usuario_e_negado(token_outro_usuario):
    with patch('api.conectar_db') as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.find_one.return_value = {
            "username": "outro_usuario", "papel": "usuario"
        }

        response = app.test_client().delete(
            "/usuarios/rafacelduds",
            headers=cabecalho_autorizacao(token_outro_usuario),
        )

        assert response.status_code == 403
        assert response.json == {"erro": "Acesso Negado"}
        colecao.delete_one.assert_not_called()
        
@patch("api.conectar_db")
def test_listar_dados_ok(mock_conectar_db):

    mock_db = mock_conectar_db.return_value
    mock_collection = mock_db.__getitem__.return_value

    usuarios = [{
        "username": "pedrow2",
        "nome": "Pedro",
        "tipo_perfil": "influenciador",
        "nicho": "moda",
        "plataformas": ["TikTok", "Instagram"],
        "descricao": "Criador de conteúdo",
    }]

    mock_collection.find.return_value = usuarios

    response = app.test_client().get("/usuarios")

    assert response.status_code == 200
    assert response.get_json() == usuarios
    mock_collection.find.assert_called_once_with(
        {"papel": "usuario"}, PROJECAO_PUBLICA
    )

@patch("api.conectar_db")
def test_listar_sem_dados(mock_conectar_db):
    colecao = mock_conectar_db.return_value.__getitem__.return_value
    colecao.find.return_value = []

    response = app.test_client().get("/usuarios")

    assert response.status_code == 200
    assert response.get_json() == []
    colecao.find.assert_called_once_with(
        {"papel": "usuario"}, PROJECAO_PUBLICA
    )


@patch("api.conectar_db")
def test_listar_filtra_por_tipo_perfil(mock_conectar_db):
    colecao = mock_conectar_db.return_value.__getitem__.return_value
    colecao.find.return_value = []

    response = app.test_client().get("/usuarios?tipo_perfil=empresa")

    assert response.status_code == 200
    colecao.find.assert_called_once_with(
        {"papel": "usuario", "tipo_perfil": "empresa"},
        PROJECAO_PUBLICA,
    )


@patch("api.conectar_db")
def test_listar_rejeita_tipo_perfil_invalido(mock_conectar_db):
    response = app.test_client().get("/usuarios?tipo_perfil=admin")

    assert response.status_code == 400
    assert response.json == {
        "erro": "tipo de perfil deve ser empresa ou influenciador"
    }
    mock_conectar_db.assert_not_called()

@pytest.mark.parametrize(
    "novo_user",
    [
        {
            "username": "pedrow2",
            "nome": "Pedro",
            "email": "pedro@email.com",
            "contato": "(11)99999-9999",
            "tipo_perfil": "influenciador",
            "nicho": "moda",
            "plataformas": ["TikTok", "Instagram"],
            "senha":"fhkjfjaskldasbf"
        },
        {
            "username": "moda_urbana",
            "nome": "Moda Urbana",
            "email": "contato@modaurbana.com",
            "contato": "(11)98888-8888",
            "tipo_perfil": "empresa",
            "nicho": "moda",
            "plataformas": ["Instagram", "TikTok"],
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
    colecao.insert_one.assert_called_once()

    usuario_salvo = colecao.insert_one.call_args.args[0]
    for campo, valor in novo_user.items():
        if campo != "senha":
            assert usuario_salvo[campo] == valor
    assert bcrypt.check_password_hash(usuario_salvo["senha"], novo_user["senha"])
    assert usuario_salvo["papel"] == "usuario"

def test_criar_user_campos_obrigatorios_ausentes():

    payload = {"username": "pedrow2", "nome": "Pedro"}
    response = app.test_client().post(
        "/usuarios",
        json=payload,
    )

    campos_obrigatorios = [
            "email",
            "contato",
            "tipo_perfil",
            "nicho",
            "plataformas",
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
        "tipo_perfil": "influenciador",
        "nicho": "moda",
        "plataformas": ["TikTok"],
        "senha": "fhkjfjaskldasbf",
    }

    response = app.test_client().post("/usuarios", json=novo_user)

    assert response.status_code == 409
    assert response.get_json() == {"erro": "Conflito: username já cadastrado"}
    colecao.find_one.assert_called_once_with({"username": "pedrow2"})
    colecao.insert_one.assert_not_called()

@patch("api.conectar_db")
def test_criar_user_armazena_senha_com_hash(mock_conectar_db):
    colecao = mock_conectar_db.return_value.__getitem__.return_value

    novo_user = {
        "username": "pedrow2",
        "nome": "Pedro",
        "email": "pedro@email.com",
        "contato": "(11)99999-9999",
        "tipo_perfil": "influenciador",
        "nicho": "moda",
        "plataformas": ["TikTok"],
        "senha": "minha_senha",
    }

    app.test_client().post("/usuarios", json=novo_user)

    usuario_salvo = colecao.insert_one.call_args.args[0]

    assert usuario_salvo["senha"] != "minha_senha"
    assert bcrypt.check_password_hash(usuario_salvo["senha"], "minha_senha")
    assert usuario_salvo["papel"] == "usuario"


@patch("api.conectar_db")
def test_criar_user_rejeita_papel_enviado_pelo_cliente(mock_conectar_db):
    response = app.test_client().post(
        "/usuarios",
        json={"papel": "admin"},
    )

    assert response.status_code == 400
    assert response.json == {"erro": "Papel é definido pelo servidor"}
    mock_conectar_db.assert_not_called()


@patch("api.conectar_db")
def test_criar_user_rejeita_tipo_perfil_invalido(mock_conectar_db):
    payload = {
        "username": "pedrow2",
        "nome": "Pedro",
        "email": "pedro@email.com",
        "contato": "(11)99999-9999",
        "tipo_perfil": [],
        "nicho": "moda",
        "plataformas": ["TikTok"],
        "senha": "minha_senha",
    }

    response = app.test_client().post("/usuarios", json=payload)

    assert response.status_code == 400
    assert response.json == {
        "erro": "Tipo de perfil deve ser Influenciador ou Empresa"
    }
    mock_conectar_db.assert_not_called()


def test_editar_user_sucesso(token_usuario):
    with patch("api.conectar_db") as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.find_one.return_value = {
            "username": "rafacelduds", "papel": "usuario"
        }
        dados = {"nome": "Rafael"}

        response = app.test_client().patch(
            "/usuarios/rafacelduds",
            json=dados,
            headers=cabecalho_autorizacao(token_usuario),
        )

        assert response.status_code == 200
        assert response.json == {"mensagem": "Usuário atualizado com sucesso"}
        colecao.update_one.assert_called_once_with(
            {"username": "rafacelduds"},
            {"$set": dados},
        )


def test_editar_user_com_descricao(token_usuario):
    with patch("api.conectar_db") as conn:
        colecao = conn.return_value.__getitem__.return_value
        dados = {
            "descricao": "Criador de conteúdo sobre moda e estilo.",
        }
        colecao.find_one.return_value = {
            "username": "rafacelduds", "papel": "usuario"
        }

        response = app.test_client().patch(
            "/usuarios/rafacelduds",
            json=dados,
            headers=cabecalho_autorizacao(token_usuario),
        )

        assert response.status_code == 200
        colecao.update_one.assert_called_once_with(
            {"username": "rafacelduds"},
            {"$set": dados},
        )


def test_editar_user_permite_descricao_vazia(token_usuario):
    with patch("api.conectar_db") as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.find_one.return_value = {
            "username": "rafacelduds", "papel": "usuario"
        }
        dados = {"descricao": ""}

        response = app.test_client().patch(
            "/usuarios/rafacelduds",
            json=dados,
            headers=cabecalho_autorizacao(token_usuario),
        )

        assert response.status_code == 200
        colecao.update_one.assert_called_once_with(
            {"username": "rafacelduds"},
            {"$set": dados},
        )


def test_editar_user_404(token_admin):
    with patch("api.conectar_db") as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.find_one.side_effect = [
            {"username": "testeadmin", "papel": "admin"},
            None,
        ]
        dados = {"descricao": "Uma descrição"}

        response = app.test_client().patch(
            "/usuarios/fulano",
            json=dados,
            headers=cabecalho_autorizacao(token_admin),
        )

        assert response.status_code == 404
        assert response.json == {"erro": "Usuário não encontrado"}
        colecao.update_one.assert_not_called()


def test_editar_user_400_sem_campos(token_usuario):
    dados = {}

    with patch("api.conectar_db") as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.find_one.return_value = {
            "username": "rafacelduds", "papel": "usuario"
        }

        response = app.test_client().patch(
            "/usuarios/rafacelduds",
            json=dados,
            headers=cabecalho_autorizacao(token_usuario),
        )

    assert response.status_code == 400
    assert response.json == {"erro": "Envie ao menos um campo para editar"}


def test_editar_user_422_campo_essencial_vazio(token_usuario):
    with patch("api.conectar_db") as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.find_one.return_value = {
            "username": "rafacelduds", "papel": "usuario"
        }

        response = app.test_client().patch(
            "/usuarios/rafacelduds",
            json={"nome": ""},
            headers=cabecalho_autorizacao(token_usuario),
        )

    assert response.status_code == 422
    assert response.json == {"erro": "Valor inválido para nome"}


def test_editar_user_409_email_em_uso(token_usuario):
    with patch("api.conectar_db") as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.find_one.side_effect = [
            {"username": "rafacelduds", "papel": "usuario"},
            {"username": "rafacelduds"},
            {"username": "outro_usuario"},
        ]
        dados = {"email": "usado@example.com"}

        response = app.test_client().patch(
            "/usuarios/rafacelduds",
            json=dados,
            headers=cabecalho_autorizacao(token_usuario),
        )

        assert response.status_code == 409
        assert response.json == {"erro": "Conflito: email já cadastrado"}
        colecao.update_one.assert_not_called()


def test_editar_user_422_plataformas_invalidas(token_usuario):
    dados = {"plataformas": []}

    with patch("api.conectar_db") as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.find_one.return_value = {
            "username": "rafacelduds", "papel": "usuario"
        }

        response = app.test_client().patch(
            "/usuarios/rafacelduds",
            json=dados,
            headers=cabecalho_autorizacao(token_usuario),
        )

    assert response.status_code == 422
    assert response.json == {"erro": "Valor inválido para plataformas"}


def test_editar_user_bloqueia_papel(token_usuario):
    with patch("api.conectar_db") as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.find_one.return_value = {
            "username": "rafacelduds", "papel": "usuario"
        }
        response = app.test_client().patch(
            "/usuarios/rafacelduds",
            json={"papel": "admin"},
            headers=cabecalho_autorizacao(token_usuario),
        )

        assert response.status_code == 400
        assert response.json == {"erro": "Papel não pode ser alterado"}
        colecao.update_one.assert_not_called()


def test_editar_user_atualiza_tipo_perfil(token_usuario):
    with patch("api.conectar_db") as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.find_one.return_value = {
            "username": "rafacelduds", "papel": "usuario"
        }

        response = app.test_client().patch(
            "/usuarios/rafacelduds",
            json={"tipo_perfil": "empresa"},
            headers=cabecalho_autorizacao(token_usuario),
        )

        assert response.status_code == 200
        colecao.update_one.assert_called_once_with(
            {"username": "rafacelduds"},
            {"$set": {"tipo_perfil": "empresa"}},
        )


def test_editar_user_rejeita_tipo_perfil_invalido(token_usuario):
    with patch("api.conectar_db") as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.find_one.return_value = {
            "username": "rafacelduds", "papel": "usuario"
        }
        response = app.test_client().patch(
            "/usuarios/rafacelduds",
            json={"tipo_perfil": "admin"},
            headers=cabecalho_autorizacao(token_usuario),
        )

        assert response.status_code == 422
        assert response.json == {
            "erro": "Tipo de perfil deve ser empresa ou influenciador"
        }
        colecao.update_one.assert_not_called()


def test_editar_user_sem_token():
    with patch("api.conectar_db") as conn:
        response = app.test_client().patch(
            "/usuarios/rafacelduds", json={"nome": "Rafael"}
        )

        assert response.status_code == 401
        conn.assert_not_called()


def test_editar_user_de_outro_usuario_e_negado(token_outro_usuario):
    with patch("api.conectar_db") as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.find_one.return_value = {
            "username": "outro_usuario", "papel": "usuario"
        }

        response = app.test_client().patch(
            "/usuarios/rafacelduds",
            json={"nome": "Rafael"},
            headers=cabecalho_autorizacao(token_outro_usuario),
        )

        assert response.status_code == 403
        assert response.json == {"erro": "Acesso Negado"}
        colecao.update_one.assert_not_called()


def test_editar_user_por_admin(token_admin):
    with patch("api.conectar_db") as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.find_one.side_effect = [
            {"username": "testeadmin", "papel": "admin"},
            {"username": "rafacelduds"},
        ]

        response = app.test_client().patch(
            "/usuarios/rafacelduds",
            json={"nome": "Rafael"},
            headers=cabecalho_autorizacao(token_admin),
        )

        assert response.status_code == 200
        colecao.update_one.assert_called_once_with(
            {"username": "rafacelduds"},
            {"$set": {"nome": "Rafael"}},
        )

def test_alterar_assinatura(token_usuario):
    with patch('api.conectar_db') as conn:
        colecao = conn.return_value.__getitem__.return_value

        colecao.find_one.return_value = {
            "username": "rafacelduds",
            "assinatura": False
        }
        colecao.update_one.return_value.matched_count = 1

        dados = {
            "assinatura": True,
            "expiracao_assinatura": "23-10-2027"
        }

        response = app.test_client().patch(
            "/usuarios/rafacelduds/assinatura",
            json=dados,
            headers=cabecalho_autorizacao(token_usuario),
        )

        assert response.status_code == 200
        assert response.json == {"mensagem": "Assinatura atualizada com sucesso"}
        colecao.find_one.assert_called_once_with(
            {"username": "rafacelduds"}
        )
        colecao.update_one.assert_called_once_with(
            {"username": "rafacelduds"},
            {"$set": dados}
        )

def test_alterar_assinatura_sem_expiracao_400(token_usuario):
    with patch('api.conectar_db') as conn:
        colecao = conn.return_value.__getitem__.return_value

        response = app.test_client().patch(
            "/usuarios/rafacelduds/assinatura",
            json={"assinatura": True},
            headers=cabecalho_autorizacao(token_usuario),
        )

        assert response.status_code == 400
        assert response.json == {"erro": "expiracao_assinatura é obrigatória para ativar a assinatura"}
        colecao.update_one.assert_not_called()

def test_desativar_assinatura(token_usuario):
    with patch("api.conectar_db") as conn:
        colecao = conn.return_value.__getitem__.return_value
        colecao.find_one.return_value = {
            "username": "rafacelduds",
            "assinatura": True,
            "expiracao_assinatura": "23-10-2027",
        }
        colecao.update_one.return_value.matched_count = 1

        response = app.test_client().patch(
            "/usuarios/rafacelduds/assinatura",
            json={"assinatura": False},
            headers=cabecalho_autorizacao(token_usuario),
        )

        assert response.status_code == 200
        colecao.update_one.assert_called_once_with(
            {"username": "rafacelduds"},
            {
                "$set": {"assinatura": False},
                "$unset": {"expiracao_assinatura": ""},
            },
        )
@patch("api.conectar_db")
def test_login_ok(mock_conectar_db):
    senha = "senha_correta"
    senha_hash = bcrypt.generate_password_hash(senha).decode("utf-8")

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
    assert set(response.json) == {"access_token"}
    with app.app_context():
        dados_token = decode_token(response.json["access_token"])
    assert dados_token["sub"] == "pedrow2"
    assert dados_token["email"] == "pedro@email.com"
    colecao.find_one.assert_called_once_with({
        "$or": [
            {"username": "pedrow2"},
            {"email": "pedrow2"}
        ]
    })

@patch("api.conectar_db")
def test_login_ok_com_email(mock_conectar_db):
    senha = "senha_correta"
    senha_hash = bcrypt.generate_password_hash(senha).decode("utf-8")

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
    assert set(response.json) == {"access_token"}
    with app.app_context():
        dados_token = decode_token(response.json["access_token"])
    assert dados_token["sub"] == "pedro"
    assert dados_token["email"] == "pedro@email.com"
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
        "senha": bcrypt.generate_password_hash("senha_correta").decode("utf-8"),
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


CHAT_URL = "/chats/influenciador_1/empresa_1"
CHAT_PARTICIPANTE = {"username": "influenciador_1"}


@patch("api.conectar_db")
def test_criar_chat_ok(mock_conectar_db, token_influenciador):
    colecao = mock_conectar_db.return_value.__getitem__.return_value
    colecao.find_one.side_effect = [CHAT_PARTICIPANTE, None]
    payload = {
        "id_influenciador": "influenciador_1",
        "id_empresa": "empresa_1",
    }

    response = app.test_client().post(
        "/chats", json=payload, headers=cabecalho_autorizacao(token_influenciador)
    )

    chat_esperado = {**payload, "conversa": [], "arquivos": []}
    assert response.status_code == 201
    assert response.get_json() == {
        "mensagem": "Chat criado com sucesso",
        "chat": chat_esperado,
    }
    colecao.insert_one.assert_called_once_with(chat_esperado)


def test_criar_chat_campos_obrigatorios_ausentes(token_influenciador):
    response = app.test_client().post(
        "/chats",
        json={"id_influenciador": "influenciador_1"},
        headers=cabecalho_autorizacao(token_influenciador),
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "erro": "Campos obrigatórios ausentes",
        "campos_faltantes": ["id_empresa"],
    }


@patch("api.conectar_db")
def test_criar_chat_duplicado(mock_conectar_db, token_influenciador):
    colecao = mock_conectar_db.return_value.__getitem__.return_value
    payload = {
        "id_influenciador": "influenciador_1",
        "id_empresa": "empresa_1",
    }
    colecao.find_one.side_effect = [CHAT_PARTICIPANTE, payload]

    response = app.test_client().post(
        "/chats", json=payload, headers=cabecalho_autorizacao(token_influenciador)
    )

    assert response.status_code == 409
    assert response.get_json() == {"erro": "Chat já existe"}
    colecao.insert_one.assert_not_called()


@patch("api.conectar_db")
def test_buscar_chat_ok(mock_conectar_db, token_influenciador):
    colecao = mock_conectar_db.return_value.__getitem__.return_value
    chat = {
        "id_influenciador": "influenciador_1",
        "id_empresa": "empresa_1",
        "conversa": [],
        "arquivos": [],
    }
    colecao.find_one.side_effect = [CHAT_PARTICIPANTE, chat]

    response = app.test_client().get(
        CHAT_URL, headers=cabecalho_autorizacao(token_influenciador)
    )

    assert response.status_code == 200
    assert response.get_json() == chat


@patch("api.conectar_db")
def test_buscar_chat_404(mock_conectar_db, token_influenciador):
    colecao = mock_conectar_db.return_value.__getitem__.return_value
    colecao.find_one.side_effect = [CHAT_PARTICIPANTE, None]

    response = app.test_client().get(
        CHAT_URL, headers=cabecalho_autorizacao(token_influenciador)
    )

    assert response.status_code == 404
    assert response.get_json() == {"erro": "Chat não encontrado"}


@patch("api.conectar_db")
def test_enviar_mensagem_ok(mock_conectar_db, token_influenciador):
    colecao = mock_conectar_db.return_value.__getitem__.return_value
    colecao.find_one.return_value = CHAT_PARTICIPANTE
    colecao.update_one.return_value.matched_count = 1
    dados = {"remetente_id": "terceiro", "mensagem": "Olá!"}

    response = app.test_client().post(
        f"{CHAT_URL}/mensagens",
        json=dados,
        headers=cabecalho_autorizacao(token_influenciador),
    )

    mensagem = {"remetente_id": "influenciador_1", "mensagem": "Olá!"}
    assert response.status_code == 201
    assert response.get_json() == {"mensagem": "Mensagem enviada com sucesso"}
    colecao.update_one.assert_called_once_with(
        {"id_influenciador": "influenciador_1", "id_empresa": "empresa_1"},
        {"$push": {"conversa": mensagem}},
    )

def test_enviar_mensagem_invalida(token_influenciador):
    response = app.test_client().post(
        f"{CHAT_URL}/mensagens",
        json={"mensagem": ""},
        headers=cabecalho_autorizacao(token_influenciador),
    )

    assert response.status_code == 422
    assert response.get_json() == {"erro": "Mensagem inválida"}


@patch("api.conectar_db")
def test_enviar_mensagem_chat_inexistente(mock_conectar_db, token_influenciador):
    colecao = mock_conectar_db.return_value.__getitem__.return_value
    colecao.find_one.return_value = CHAT_PARTICIPANTE
    colecao.update_one.return_value.matched_count = 0

    response = app.test_client().post(
        f"{CHAT_URL}/mensagens",
        json={"mensagem": "Olá!"},
        headers=cabecalho_autorizacao(token_influenciador),
    )

    assert response.status_code == 404
    assert response.get_json() == {"erro": "Chat não encontrado"}


@patch("api.conectar_db")
def test_adicionar_arquivo_ok(mock_conectar_db, token_influenciador):
    colecao = mock_conectar_db.return_value.__getitem__.return_value
    colecao.find_one.return_value = CHAT_PARTICIPANTE
    colecao.update_one.return_value.matched_count = 1
    arquivo = {
        "nome": "proposta.pdf",
        "url": "https://exemplo.com/proposta.pdf",
        "tipo": "application/pdf",
    }

    response = app.test_client().post(
        f"{CHAT_URL}/arquivos",
        json=arquivo,
        headers=cabecalho_autorizacao(token_influenciador),
    )

    assert response.status_code == 201
    assert response.get_json() == {"mensagem": "Arquivo adicionado com sucesso"}
    colecao.update_one.assert_called_once_with(
        {"id_influenciador": "influenciador_1", "id_empresa": "empresa_1"},
        {"$push": {"arquivos": arquivo}},
    )


def test_adicionar_arquivo_campos_obrigatorios_ausentes(token_influenciador):
    response = app.test_client().post(
        f"{CHAT_URL}/arquivos",
        json={"nome": "proposta.pdf"},
        headers=cabecalho_autorizacao(token_influenciador),
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "erro": "Campos obrigatórios ausentes",
        "campos_faltantes": ["url", "tipo"],
    }

@pytest.mark.parametrize(
    ("metodo", "url", "dados"),
    [
        ("POST", "/chats", {"id_influenciador": "influenciador_1", "id_empresa": "empresa_1"}),
        ("GET", CHAT_URL, None),
        ("POST", f"{CHAT_URL}/mensagens", {"mensagem": "Olá!"}),
        ("POST", f"{CHAT_URL}/arquivos", {"nome": "a.pdf", "url": "https://exemplo.com/a.pdf", "tipo": "application/pdf"}),
    ],
    ids=["criar", "buscar", "mensagem", "arquivo"],
)
@patch("api.conectar_db")
def test_chat_de_outro_usuario_e_negado(
    mock_conectar_db, token_outro_usuario, metodo, url, dados
):
    colecao = mock_conectar_db.return_value.__getitem__.return_value
    colecao.find_one.return_value = {"username": "outro_usuario"}

    response = app.test_client().open(
        url,
        method=metodo,
        json=dados,
        headers=cabecalho_autorizacao(token_outro_usuario),
    )

    assert response.status_code == 403
    assert response.json == {"erro": "Acesso Negado"}


def test_chat_sem_token():
    response = app.test_client().get(CHAT_URL)

    assert response.status_code == 401


@patch("api.conectar_db")
def test_chat_usuario_do_token_inexistente(mock_conectar_db, token_influenciador):
    colecao = mock_conectar_db.return_value.__getitem__.return_value
    colecao.find_one.return_value = None

    response = app.test_client().get(
        CHAT_URL, headers=cabecalho_autorizacao(token_influenciador)
    )

    assert response.status_code == 401
    assert response.json == {"erro": "Usuário não encontrado"}


def test_adicionar_avaliacao_201():
    with patch('api.conectar_db') as conn:
        colecao = conn.return_value.__getitem__.return_value

        colecao.find_one.return_value = {
            "username": "testeuser"
        }

        payload = {
            "username": "rafacelduds",
            "alvo": "testeuser",
            "nota": 5,
            "conteudo": "Trata o trabalho com seriedade"
        }

        response = app.test_client().post("/usuarios/testeuser/avaliacoes", json=payload)

        assert response.status_code == 201
        assert response.json == {"mensagem": "Avaliação feita com sucesso"}
        colecao.insert_one.assert_called_once_with(payload)

@pytest.mark.parametrize("payload", [None, [], "texto", 5])
def test_adicionar_avaliacao_json_invalido(payload):
    with patch("api.conectar_db") as conn:
        response = app.test_client().post(
            "/usuarios/testeuser/avaliacoes",
            json=payload,
        )

        assert response.status_code == 400
        assert response.json == {"erro": "JSON inválido"}
        conn.assert_not_called()

def test_adicionar_avaliacao_campos_ausentes():
    with patch("api.conectar_db") as conn:
        response = app.test_client().post(
            "/usuarios/testeuser/avaliacoes",
            json={"username": "rafacelduds"},
        )

        assert response.status_code == 400
        assert response.json == {
            "erro": "Campos obrigatórios ausentes",
            "campos_faltantes": ["alvo", "nota", "conteudo"],
        }
        conn.assert_not_called()

@pytest.mark.parametrize("campo", ["username", "alvo", "conteudo"])
@pytest.mark.parametrize("valor", ["", "   ", None, 123, [], {}])
def test_adicionar_avaliacao_campo_invalido(campo, valor):
    with patch("api.conectar_db") as conn:
        payload = {
            "username": "rafacelduds",
            "alvo": "testeuser",
            "nota": 5,
            "conteudo": "Trata o trabalho com seriedade",
        }
        payload[campo] = valor

        response = app.test_client().post(
            "/usuarios/testeuser/avaliacoes",
            json=payload,
        )

        assert response.status_code == 422
        assert response.json == {
            "erro": f"Valor inválido para {campo}"
        }
        conn.assert_not_called()

@pytest.mark.parametrize(
    "nota",
    [0, 6, -1, 3.5, "5", None, True, False, [], {}],
)
def test_adicionar_avaliacao_nota_invalida(nota):
    with patch("api.conectar_db") as conn:
        payload = {
            "username": "rafacelduds",
            "alvo": "testeuser",
            "nota": nota,
            "conteudo": "Trata o trabalho com seriedade",
        }

        response = app.test_client().post(
            "/usuarios/testeuser/avaliacoes",
            json=payload,
        )

        assert response.status_code == 422
        assert response.json == {
            "erro": "nota deve ser um inteiro entre 1 e 5"
        }
        conn.assert_not_called()

def test_adicionar_avaliacao_alvo_diferente_da_url():
    with patch("api.conectar_db") as conn:
        payload = {
            "username": "rafacelduds",
            "alvo": "outro_usuario",
            "nota": 5,
            "conteudo": "Trata o trabalho com seriedade",
        }

        response = app.test_client().post(
            "/usuarios/testeuser/avaliacoes",
            json=payload,
        )

        assert response.status_code == 422
        assert response.json == {
            "erro": "alvo deve corresponder ao usuário da URL"
        }
        conn.assert_not_called()

def test_adicionar_avaliacao_usuario_inexistente():
    with patch("api.conectar_db") as conn:
        db = conn.return_value
        usuarios = db["tbl_testes"]
        avaliacoes = db["tbl_avaliacoes"]

        usuarios.find_one.return_value = None

        payload = {
            "username": "rafacelduds",
            "alvo": "testeuser",
            "nota": 5,
            "conteudo": "Trata o trabalho com seriedade",
        }

        response = app.test_client().post(
            "/usuarios/testeuser/avaliacoes",
            json=payload,
        )

        assert response.status_code == 404
        assert response.json == {
            "erro": "Usuário alvo não encontrado"
        }
        usuarios.find_one.assert_called_once_with(
            {"username": "testeuser"}
        )
        avaliacoes.insert_one.assert_not_called()


def test_adicionar_avaliacao_avaliador_inexistente():
    with patch("api.conectar_db") as conn:
        db = conn.return_value
        usuarios = db["tbl_testes"]
        usuarios.find_one.side_effect = [
            {"username": "testeuser"},
            None,
        ]

        response = app.test_client().post(
            "/usuarios/testeuser/avaliacoes",
            json={
                "username": "rafacelduds",
                "alvo": "testeuser",
                "nota": 5,
                "conteudo": "Trata o trabalho com seriedade",
            },
        )

        assert response.status_code == 404
        assert response.json == {"erro": "Usuário criador não encontrado"}
        db["tbl_avaliacoes"].insert_one.assert_not_called()
