# projeto-3-backend

## Preferências de aparência da conta

`GET /usuarios/me/preferencias` e `PATCH /usuarios/me/preferencias` exigem JWT.
O usuário é sempre o `sub` autenticado; não aceitam seleção de outra conta.
Contas inexistentes/desativadas recebem `401` e token ausente/inválido é recusado.

O GET retorna `{"preferencias": {...}}`. Contas antigas sem preferências recebem
os padrões sem precisar de migração: `tema: "claro"`, `fonte: "padrao"`,
`cor: "verde"`, `reduzirMovimento: false`, `altoContraste: false`.

O PATCH aceita um objeto não vazio com um ou mais desses campos:

| Campo | Valores aceitos |
| --- | --- |
| `tema` | `claro`, `escuro`, `sistema` |
| `fonte` | `padrao`, `grande`, `maior` |
| `cor` | `verde`, `azul`, `ameixa` |
| `reduzirMovimento` / `altoContraste` | Booleanos JSON |

Exemplo: `{"tema":"escuro","altoContraste":true}`. Responde `200` com todas
as preferências atuais. Campos desconhecidos, tipos ou valores inválidos geram
`400`, sem gravação. Falha de persistência retorna `503`, sem confirmar sucesso.
Campos enviados são atualizados atomicamente em `tbl_usuarios.preferencias`,
preservando os não enviados e sem modificar perfil ou pagamento. As consultas
públicas de usuários não expõem essas preferências. Não há chamada ao Paddle.

## Pagamentos e assinaturas Paddle Sandbox

O backend cria checkouts, recebe notificações assinadas, ativa/renova o Premium
pelo período pago e agenda cancelamento sem retirar acesso antes da validade.
Não há opção de produção: somente `https://sandbox-api.paddle.com`, API key
`pdl_sdbx_apikey_` e token público `test_` são aceitos.

Configure no `.env` local, conforme `.env.example`:

- `PADDLE_API_KEY`: chave secreta de sandbox.
- `PADDLE_CLIENT_TOKEN`: token público para Paddle.js.
- `PADDLE_PREMIUM_PRICE_ID`: preço mensal de R$ 29,90 em BRL, sem trial.
- `PADDLE_WEBHOOK_SECRET`: endpoint secret key do destino de notificações.

Rotas autenticadas: `POST /usuarios/me/assinatura/checkout`,
`POST /usuarios/me/assinatura/cancelamento` e `GET /me`.
Envie corpo vazio ou `{}` aos POSTs; o usuário vem do JWT.

`POST /webhooks/pagamentos` dispensa JWT e exige `Paddle-Signature` válida sobre
o corpo original, com timestamp de até cinco segundos. Os eventos usados são
`transaction.completed`, `transaction.payment_failed` e `subscription.created`,
`activated`, `updated`, `canceled`, `paused`, `past_due` e `resumed` (todos os
últimos com prefixo `subscription.`). O backend consulta o Paddle e
confere preço e vínculo com o checkout antes de conceder acesso. O callback do
navegador não concede Premium. A rota manual de assinatura agora retorna `410`.

O campo `pagamento.premium_ate` usa timestamp UTC com fuso; o acesso termina no instante
exato. Falhas de renovação não estendem o período pago. Cancelamento é agendado
para o fim do período, e o usuário mantém acesso até essa data. Idempotência
usa eventos únicos e validade absoluta, com reserva por usuário para serializar
atualizações. Não é necessário um job para bloquear acesso após a expiração:
a validade é conferida a cada operação protegida.

**Ainda pendente:** validar o checkout completo e a entrega externa dos eventos
pelos túneis configurados no Paddle. O código e as simulações locais já foram
verificados. Consulte o roteiro e os critérios em `mudancas.md`.

Reservas de checkout `needs_review` exigem conferência da referência no sandbox
antes de permitir nova compra. Não apagar uma reserva incerta sem conferir se
a transação foi criada pelo provedor. Nunca commitar o `.env`.

## Postagens

`GET /postagens` retorna o feed de todos os usuários, sem exigir token, incluindo
as publicações do próprio usuário. Cada item inclui `autor` com apenas `nome`,
`username` e `tipo_perfil`; o valor é `null` quando o perfil não existe mais.
Os dados do autor são consultados no perfil atual. Nenhum dado de contato,
e-mail ou senha é incluído. O frontend ordena as publicações da mais recente
para a mais antiga e abre a criação pelo botão “Nova postagem”.

Postagens aceitam de um a três arquivos de até 8 MB cada. Envie `POST /postagens`
com token Bearer, `titulo`, `descricao` e o campo multipart `midia` repetido para
cada arquivo. O retorno contém `postagem.midias` com os metadados em ordem;
`midia` continua representando o primeiro arquivo para compatibilidade.

`GET /postagens/:id/midia/:indice` baixa um arquivo pelo índice (0 a 2).
`GET /postagens/:id/midia` continua baixando o primeiro arquivo, inclusive de
postagens antigas. Listagem e detalhes retornam metadados sem conteúdo binário.

No `PATCH /postagens/:id`, enviar arquivos substitui todos os anexos; enviar
somente título/descrição preserva os anexos existentes. Os limites também se
aplicam à edição. Exclusão e substituição removem os arquivos extras associados.
O primeiro arquivo fica no documento da postagem e os demais em
`tbl_midias_postagens`, evitando ultrapassar os 16 MB por documento do MongoDB.

## Acesso Premium ao chat

Todas as operações de chat exigem JWT, participação na conversa e assinatura
Premium ativa do usuário autenticado, tanto empresa quanto influenciador:

| Método | Rota |
| --- | --- |
| POST | `/chats` |
| GET | `/chats/:id_influenciador/:id_empresa` |
| POST | `/chats/:id_influenciador/:id_empresa/mensagens` |
| POST | `/chats/:id_influenciador/:id_empresa/arquivos` |

O backend consulta `pagamento.status` e `pagamento.premium_ate` em `tbl_usuarios`.
Status ativo, pendente ou cancelado mantém apenas o período já pago; pausa
bloqueia o acesso. Campos antigos continuam legíveis até a migração.
Assinatura expirada, pausada ou sem data válida recebe
`403` com `codigo: "premium_necessario"`; nenhum chat é consultado ou alterado.
Não basta a outra parte ser Premium, e dados enviados no JSON não concedem acesso.
Usuário externo à conversa continua recebendo `403` por falta de participação.

O fechamento de contratos será exclusivo para Premium, mas não existe endpoint
de contratos neste backend. A página `/contratos` do frontend verifica a
assinatura e informa que o fluxo está em construção. Quando implementado, o
endpoint de fechamento deverá reutilizar `assinatura_premium_ativa` e validar a
participação e a assinatura de cada usuário que realizar uma ação no contrato.
As análises públicas do YouTube estão descritas abaixo. A diferenciação entre
análise parcial e completa por plano, destaque nas buscas e consulta de comentários
de empresas permanecem benefícios planejados na página de planos.

## Organização dos pagamentos

Os dados persistidos ficam em `pagamento`: `assinatura_id`, `cliente_id`,
`status`, `premium_ate`, `cancelamento_agendado` e `proxima_renovacao`.
`assinatura`, `renovacao_automatica` e `falha_pagamento` são calculados nas
respostas privadas da API; não são mais gravados no documento. O frontend
continua recebendo os mesmos campos, sem acesso ao ID interno do cliente.
Notificações e cancelamentos convertem registros antigos ao atualizar o estado.

Para converter os registros existentes, reinicie o backend com esta versão e
execute `python migrar_pagamentos.py` para simular ou
`python migrar_pagamentos.py --aplicar` para gravar no banco do `.env`.
A migração preserva o período pago, remove campos duplicados e pode ser repetida.
Registros inconsistentes ou em atualização ficam pendentes, sem alterar o acesso.
As coleções de checkout/eventos e os bloqueios temporários continuam necessários.

## Análises públicas do YouTube

O backend oferece três endpoints autenticados para a primeira integração de
análise de influenciadores:

- `POST /analises/youtube` recebe `{"url": "https://www.youtube.com/@canal"}`,
  consulta os dados públicos do canal e até 10 vídeos recentes. Cada chamada
  salva ou atualiza a captura daquele dia no MongoDB, associada ao
  influenciador autenticado no sistema.
- `GET /analises/youtube` retorna até 90 capturas diárias do influenciador
  autenticado, em ordem cronológica, para alimentar gráficos.
- `GET /analises/youtube/:username` consulta o histórico do influenciador
  identificado pelo username. Qualquer empresa cadastrada pode consultar qualquer
  influenciador, sem exigir Premium, vínculo ou campanha. Influenciadores podem
  consultar somente o próprio histórico por essa rota.

Envie `Authorization: Bearer <token>` em todas as rotas. Por exemplo,
uma empresa consulta `GET /analises/youtube/duds`. As duas rotas GET retornam
`{"plataforma": "youtube", "historico": [...]}`, com o mesmo formato de capturas,
até 90 registros mais recentes em ordem cronológica e `coletado_em` em ISO 8601.
As consultas leem apenas o histórico salvo e não chamam o YouTube.
Perfil sem capturas retorna `200` com `historico: []`.

Na consulta por username, token ausente/inválido ou conta autenticada removida
impede o acesso; conta removida retorna `401`. Acesso a outro influenciador por
um influenciador retorna `403`. Alvo inexistente ou que não seja influenciador
retorna `404`. O GET original continua consultando somente o usuário do token.

Para executar os testes locais de acesso e histórico (MongoDB simulado):
`python -m pytest test_analises_youtube.py -q`.

A autenticação associa a análise à conta do sistema, mas não comprova que o
canal do YouTube informado pertence àquela pessoa.
Para manter o histórico crescendo, o endpoint precisa ser chamado periodicamente;
por enquanto, não há tarefa automática agendada.

São aceitos links `youtube.com/@canal`, `youtube.com/channel/ID_DO_CANAL` e
`youtube.com/user/usuario`. Os dados incluem inscritos (quando visíveis),
visualizações totais, número de vídeos e visualizações/curtidas/comentários dos
vídeos recentes. Alcance e impressões privadas não são coletados.

Configure `YOUTUBE_API_KEY` no ambiente do backend (por exemplo, no `.env`, que
não deve ser versionado):

```text
YOUTUBE_API_KEY=sua-chave-da-youtube-data-api
```

A YouTube Data API usa cotas por projeto. O endpoint faz até três consultas
para cada análise: canal, playlist de envios e estatísticas dos vídeos.
