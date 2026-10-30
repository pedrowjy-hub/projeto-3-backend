import copy
from unittest.mock import MagicMock, patch

import pytest
from flask_jwt_extended import create_access_token
from pymongo.errors import PyMongoError

from api import app
from preferencias import PADRAO

URL = '/usuarios/me/preferencias'


@pytest.fixture
def context(monkeypatch):
    monkeypatch.setitem(app.config, 'JWT_SECRET_KEY', 'preferencias-testes-chave-isolada-1234567890')
    users = {'ana': {'username': 'ana', 'ativo': True}, 'bia': {'username': 'bia', 'ativo': True},
             'inativa': {'username': 'inativa', 'ativo': False}}
    collection = MagicMock()
    collection.find_one.side_effect = lambda query, *_args: copy.deepcopy(users.get(query['username']))
    def update(query, change, **_kwargs):
        user = users.get(query['username'])
        if not user or user.get('ativo') is False:
            return None
        for key, value in change['$set'].items():
            user.setdefault('preferencias', {})[key.split('.', 1)[1]] = value
        return copy.deepcopy(user)
    collection.find_one_and_update.side_effect = update
    def headers(username='ana'):
        with app.app_context():
            return {'Authorization': 'Bearer ' + create_access_token(identity=username)}
    with patch('api.conectar_db', return_value={'tbl_usuarios': collection}):
        yield app.test_client(), headers, users, collection


def test_conta_antiga_recebe_padrao_sem_criar_dados(context):
    client, headers, users, collection = context
    response = client.get(URL, headers=headers())
    assert response.status_code == 200
    assert response.json == {'preferencias': PADRAO}
    assert 'preferencias' not in users['ana']
    collection.find_one_and_update.assert_not_called()


def test_salva_e_recupera_em_nova_sessao_preservando_campos_nao_enviados(context):
    client, headers, users, _ = context
    users['ana'].update(pagamento={'status': 'active'}, email='ana@example.com')
    assert client.patch(URL, headers=headers(), json={'tema': 'escuro', 'altoContraste': True}).status_code == 200
    assert client.patch(URL, headers=headers(), json={'fonte': 'maior'}).status_code == 200
    response = client.get(URL, headers=headers())
    assert response.json['preferencias'] == {**PADRAO, 'tema': 'escuro', 'altoContraste': True, 'fonte': 'maior'}
    assert users['ana']['pagamento'] == {'status': 'active'}
    assert users['ana']['email'] == 'ana@example.com'
    assert client.get(URL, headers=headers('bia')).json == {'preferencias': PADRAO}


@pytest.mark.parametrize('payload', [{}, [], None, {'username': 'bia'}, {'pagamento': {}},
    {'tema': 'qualquer'}, {'fonte': 42}, {'cor': '<script>'}, {'altoContraste': 'true'},
    {'reduzirMovimento': 1}, {'altoContraste': True, 'tema': []}])
def test_recusa_payload_invalido_sem_gravar(context, payload):
    client, headers, _, collection = context
    assert client.patch(URL, headers=headers(), json=payload).status_code == 400
    collection.find_one_and_update.assert_not_called()


@pytest.mark.parametrize('method', ['GET', 'PATCH'])
@pytest.mark.parametrize('username', [None, 'inativa', 'removida'])
def test_sem_login_conta_desativada_ou_removida_nao_acessa(context, method, username):
    client, headers, _, collection = context
    response = client.open(URL, method=method, json={'tema': 'escuro'}, headers=headers(username) if username else {})
    assert response.status_code == 401
    collection.find_one_and_update.assert_not_called()


def test_query_nao_escolhe_outro_usuario_e_restauracao_grava_na_conta(context):
    client, headers, users, _ = context
    client.patch(URL+'?username=bia', headers=headers(), json={'cor': 'azul'})
    assert users['ana']['preferencias']['cor'] == 'azul'
    assert 'preferencias' not in users['bia']
    assert client.patch(URL, headers=headers(), json=PADRAO).json == {'preferencias': PADRAO}


def test_falha_banco_nao_e_reportada_como_sucesso(context):
    client, headers, _, collection = context
    collection.find_one_and_update.side_effect = PyMongoError('indisponivel')
    assert client.patch(URL, headers=headers(), json={'tema': 'escuro'}).status_code == 503


def test_campos_publicos_nao_expoem_preferencias():
    from api import CAMPOS_PUBLICOS
    assert 'preferencias' not in CAMPOS_PUBLICOS
