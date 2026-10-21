Para o modelo “admin” e “usuário normal” (influenciador ou empresa), eu aplicaria assim:

| Rota | JWT | Regra adicional |
|---|---:|---|
| `POST /login` | Não | Pública; recebe credenciais e devolve token. |
| `POST /usuarios` | Não | Pública para cadastro, mas o servidor nunca pode aceitar `tipo: "admin"` vindo do cliente. Permita apenas `influenciador` ou `empresa`. |
| `GET /usuarios` | Depende | Pública se for uma vitrine/listagem; nesse caso, nunca retorne `senha`, e idealmente nem e-mail e contato. Se for gestão de usuários, apenas admin. |
| `GET /usuarios/<username>` | Depende | Pública se for perfil público, com projeção de campos seguros. Se mostrar dados privados, somente o dono ou admin. |
| `PATCH /usuarios/<username>` | Sim | Usuário normal altera apenas o próprio perfil; admin pode alterar outros. Usuário normal não pode mudar `tipo` para admin. |
| `DELETE /usuarios/<username>` | Sim | Apenas o próprio usuário ou admin. |
| `PATCH /usuarios/<username>/assinatura` | Sim | Admin, ou integração de pagamento autenticada. Um usuário não deve poder definir a própria assinatura como ativa. |
| `POST /chats` | Sim | Identidade do token deve ser um dos dois participantes. |
| `GET /chats/...` | Sim | Identidade do token deve pertencer à conversa. |
| `POST .../mensagens` | Sim | Identidade deve ser participante; derive o remetente pelo token, não pelo JSON enviado. |
| `POST .../arquivos` | Sim | Identidade deve ser participante. |
| `POST /usuarios/<username>/avaliacoes` | Sim | Derive o avaliador pelo token; não aceite `username` como identidade confiável no corpo. |
| `GET /admin` | Sim | Além do JWT, verificar no banco se `tipo == "admin"`. |

O ponto-chave é: `@jwt_required()` só confirma que existe um token válido. Para as rotas acima, você também precisa comparar a identidade do token (`get_jwt_identity()`) com o usuário da URL, o participante do chat ou o papel de admin.

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
