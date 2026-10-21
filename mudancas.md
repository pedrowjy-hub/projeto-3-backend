Para o modelo “admin” e “usuário normal” (influenciador ou empresa), eu aplicaria assim:

## Autorização das rotas protegidas

`@jwt_required()` resolve apenas a autenticação: confirma que a requisição
possui um token válido e disponibiliza a identidade do usuário com
`get_jwt_identity()`. Cada rota também deve aplicar a regra de autorização
adequada. Neste projeto, a identidade do token é o `username`.

Mantenha o papel de administrador no banco como fonte de verdade. Não confie
apenas em uma claim do token para essa decisão, pois o usuário pode ter sido
removido ou ter seu papel alterado depois da emissão do token.

### Usuário dono do perfil ou administrador

Use este decorador em operações sobre `/usuarios/<username>` que podem ser
feitas pelo próprio titular ou por um administrador:

```python
from functools import wraps
from flask import jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity


def proprio_usuario_ou_admin_required(func):
    @wraps(func)
    @jwt_required()
    def wrapper(*args, **kwargs):
        username_logado = get_jwt_identity()
        usuario_logado = conectar_db()["tbl_testes"].find_one(
            {"username": username_logado},
            {"_id": 0, "tipo": 1},
        )

        if usuario_logado is None:
            return jsonify({"erro": "Usuário não encontrado"}), 401

        username_alvo = kwargs["username"]
        eh_admin = usuario_logado.get("tipo") == "admin"

        if username_logado != username_alvo and not eh_admin:
            return jsonify({"erro": "Acesso negado"}), 403

        return func(*args, **kwargs)

    return wrapper
```

Aplicar em `PATCH /usuarios/<username>` e `DELETE /usuarios/<username>`:

```python
@app.route("/usuarios/<string:username>", methods=["PATCH"])
@proprio_usuario_ou_admin_required
def editar_user(username):
    ...
```

### Códigos de resposta

- `401`: token ausente, inválido ou referente a usuário que não existe mais.
- `403`: token válido, mas usuário sem permissão para aquela ação.

| Rota | JWT | Regra adicional |
|---|---:|---|
| `POST /login` | Não | Pública; recebe credenciais e devolve token. |
| `POST /usuarios` | Não | Pública para cadastro, mas o servidor nunca pode aceitar `tipo: "admin"` vindo do cliente. Permita apenas `influenciador` ou `empresa`. |
| `GET /usuarios` | Depende | Pública se for uma vitrine/listagem; nesse caso, nunca retorne `senha`, e idealmente nem e-mail e contato. Se for gestão de usuários, apenas admin. |
| `GET /usuarios/<username>` | Depende | Pública se for perfil público, com projeção de campos seguros. Se mostrar dados privados, somente o dono ou admin. |
| `PATCH /usuarios/<username>` | Sim | Usuário normal altera apenas o próprio perfil; admin pode alterar outros. Usuário normal não pode mudar `tipo` para admin. |
| `DELETE /usuarios/<username>` | Sim | Apenas o próprio usuário ou admin. |
| `PATCH /usuarios/<username>/assinatura` | Sim | Na simulação, apenas o próprio usuário pode alterar o estado. Em pagamento real, substituir por checkout, cancelamento e webhook. |
| `POST /chats` | Sim | Identidade do token deve ser um dos dois participantes. |
| `GET /chats/...` | Sim | Identidade do token deve pertencer à conversa. |
| `POST .../mensagens` | Sim | Identidade deve ser participante; derive o remetente pelo token, não pelo JSON enviado. |
| `POST .../arquivos` | Sim | Identidade deve ser participante. |
| `POST /usuarios/<username>/avaliacoes` | Sim | Derive o avaliador pelo token; não aceite `username` como identidade confiável no corpo. |
| `GET /admin` | Sim | Além do JWT, verificar no banco se `tipo == "admin"`. |

### Assinatura e pagamento

O decorador controla quem pode chamar uma rota; ele não processa nem confirma
um pagamento. A atual `PATCH /usuarios/<username>/assinatura` funciona como
uma simulação de plano: depois de protegida com
`@jwt_required()` e uma comparação entre a identidade do token e o `username`
da URL, o usuário consegue alterar somente a própria assinatura. Não inclua a
exceção de administrador nessa rota se o administrador não tiver essa função
de suporte no produto.

Para uma assinatura paga real, o cliente nunca deve enviar diretamente
`assinatura: true` ou `expiracao_assinatura`. O fluxo deve ser:

```text
usuário autenticado → backend cria checkout → pagamento confirmado
→ webhook verificado do provedor → backend atualiza a assinatura
```

Nesse cenário, prefira estas rotas:

| Rota | Regra |
|---|---|
| `POST /usuarios/me/assinatura/checkout` | Requer JWT e inicia o pagamento do usuário logado. |
| `POST /usuarios/me/assinatura/cancelamento` | Requer JWT e permite cancelar apenas o próprio plano. |
| `POST /webhooks/pagamentos` | Recebe e valida a assinatura do provedor; é a única rota que ativa, renova ou define a expiração. |

Para desenvolvimento, eu deixaria algo como:

```python
CORS(app, origins=["http://localhost:5173"])
app.run(debug=True)
```

No deploy, use variáveis de ambiente para desligar `debug` e configurar somente a origem real do frontend. O problema principal permanece sendo autorização: hoje apenas `/admin` usa JWT, então as demais rotas não distinguem quem está chamando a API.


| Objetivo | Rota sugerida | JWT |
|---|---|---:|
| Listar empresas e influenciadores disponíveis | `GET /perfis` | Não |
| Ver perfil público | `GET /perfis/<username>` | Não |
| Listar todos os usuários para gestão | `GET /usuarios` | Sim, admin |
| Ver dados completos de um usuário | `GET /usuarios/<username>` | Sim, próprio usuário ou admin |

`GET /perfis` deve retornar somente dados de vitrine, por exemplo:

```json
{
  "username": "ana-criadora",
  "nome": "Ana",
  "tipo_perfil": "influenciador",
  "nicho": "Moda",
  "plataformas": ["Instagram"],
  "descricao": "..."
}
```

Nunca inclua `senha` (nem hash), e-mail, contato, assinatura ou dados administrativos nessas rotas públicas. Também filtre usuários `admin`, para que eles não apareçam como perfis disponíveis.

Eu separaria o modelo assim:

```json
{
  "papel": "admin | usuario",
  "tipo_perfil": "empresa | influenciador"
}
```
