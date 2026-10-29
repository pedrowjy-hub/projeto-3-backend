# projeto-3-backend

## Checkout Paddle em sandbox

A branch de pagamentos implementa o checkout, sem ativação de Premium por webhook.
Configure `PADDLE_API_KEY`, `PADDLE_CLIENT_TOKEN` e `PADDLE_PREMIUM_PRICE_ID` no
`.env` local (modelo em `.env.example`) e reinicie o backend. A chave precisa
começar com `pdl_sdbx_apikey_` e o token público com `test_`. O único destino da
API é `https://sandbox-api.paddle.com`; nenhuma opção de produção é aceita.

`POST /usuarios/me/assinatura/checkout`, autenticado com Bearer e corpo `{}`,
confere o preço de R$ 29,90/mês no Paddle e cria/reutiliza uma transação de
teste vinculada ao usuário do JWT. O retorno contém `transaction_id`,
`client_token` público e `environment: "sandbox"`, para abrir o Paddle.js.
O endpoint não altera `assinatura` ou `expiracao_assinatura`.

No painel de sandbox do Paddle, configure o **Default payment link**, em
**Checkout > Checkout settings/configuration**. Nesta conta, o Paddle recusou
`https://localhost/`, apesar da sugestão no guia de testes. Falta uma URL pública
válida. O frontend inclui `public/paddle-checkout.html`, pronto para hospedagem
estática mediante autorização do usuário, sem publicar o backend. O teste real
de criação ainda retorna erro pela ausência desse campo; a abertura do checkout
não foi validada no navegador. O frontend principal pode continuar local.

Cliques repetidos reutilizam o checkout pendente. Falhas com resultado incerto
ficam registradas como `needs_review` em `tbl_checkout_pagamentos` e bloqueiam
nova compra até conferência no sandbox. Não apagar esse registro sem verificar
se houve criação externa. O ID pode ser recuperado pela referência de
`custom_data`, antes de continuar a integração.

Com Paddle configurado, a rota antiga de alteração manual de assinatura fica
bloqueada. Webhooks, liberação/renovação do Premium e cancelamento pelo aplicativo
ainda estão pendentes. Para encerrar assinaturas de testes, use o painel Paddle
Sandbox. Veja o roteiro e os próximos passos em `mudancas.md`.

Os testes de `test_checkout_pagamentos.py` exercitam o Flask e o cliente HTTP
contra um servidor local de simulação, sem chamar a API real nem exigir segredos.

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

O backend consulta `assinatura` e `expiracao_assinatura` em `tbl_usuarios`.
É necessário `assinatura == true` e validade `DD-MM-AAAA` igual ou posterior ao
dia atual em Brasília. Assinatura cancelada, expirada ou sem data válida recebe
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
