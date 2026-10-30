# projeto-3-backend

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
o corpo original, com timestamp de até cinco segundos. Configure eventos de
transação e assinatura conforme `mudancas.md`. O backend consulta o Paddle e
confere preço e vínculo com o checkout antes de conceder acesso. O callback do
navegador não concede Premium. A rota manual de assinatura agora retorna `410`.

O campo `pagamento.premium_ate` usa timestamp UTC com fuso; o acesso termina no instante
exato. Falhas de renovação não estendem o período pago. Cancelamento é agendado
para o fim do período, e o usuário mantém acesso até essa data. Idempotência
usa eventos únicos e validade absoluta, com reserva por usuário para serializar
atualizações. Não é necessário um job para bloquear acesso após a expiração:
a validade é conferida a cada operação protegida.

**Ainda pendente no ambiente:** Default payment link válido no Paddle e URL
HTTPS para entregar os webhooks ao backend. O link temporário/deploy será
resolvido depois; nenhuma página foi publicada. Abertura do checkout e entrega
externa dos eventos não foram homologadas, apenas o código e as simulações
locais. Consulte o roteiro e os critérios em `mudancas.md`.

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
Análises, destaque nas buscas e consulta de comentários de empresas permanecem
benefícios planejados, sinalizados como em construção na página de planos.

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
