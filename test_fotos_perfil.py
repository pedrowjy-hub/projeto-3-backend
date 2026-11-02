import base64
from io import BytesIO
from unittest.mock import patch, MagicMock
from collections import defaultdict
import pytest
from PIL import Image
from flask_jwt_extended import create_access_token
from fotos_perfil import normalizar_foto
from api import app, CAMPOS_PUBLICOS

def photo():
    out=BytesIO(); Image.new('RGB', (20, 40), 'red').save(out, format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()

def test_reencode():
    output=normalizar_foto(photo())
    with Image.open(BytesIO(base64.b64decode(output.split(',')[1]))) as image:
        assert image.size == (512,512) and image.format == 'JPEG'

@pytest.mark.parametrize('value', [None, {}, 2, 'https://site/foto.png', 'data:image/svg+xml;base64,PHN2Zz4=', 'data:image/jpeg;base64,invalid', 'x'*500001], ids=['null','object','number','url','svg','invalid_base64','oversized'])
def test_reject_invalid(value):
    with pytest.raises(ValueError): normalizar_foto(value)

def test_remove():
    assert normalizar_foto('') == ''

def test_owner_can_update_and_other_user_cannot():
    with patch.dict(app.config, TESTING=True, JWT_SECRET_KEY='foto-testes-chave-isolada-123456789'), patch('api.conectar_db', return_value=defaultdict(MagicMock)) as conn:
        db=conn.return_value
        db['tbl_usuarios'].find_one.return_value={'username':'ana','papel':'usuario','ativo':True}
        with app.app_context(): headers={'Authorization':'Bearer '+create_access_token(identity='ana')}
        client=app.test_client()
        assert client.patch('/usuarios/ana', json={'foto_perfil':photo()}, headers=headers).status_code == 200
        assert db['tbl_usuarios'].update_one.call_args.args[1]['$set']['foto_perfil'].startswith('data:image/jpeg;base64,')
        assert client.patch('/usuarios/outra', json={'foto_perfil':photo()}, headers=headers).status_code == 403
        assert CAMPOS_PUBLICOS['foto_perfil'] == 1
