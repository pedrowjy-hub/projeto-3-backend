## Avaliações de influenciadores

Separar a nota pública dos comentários detalhados. O alvo deve ser um perfil
com `tipo_perfil == "influenciador"`; caso contrário, retornar `404`.

| Rota | Acesso | Regra |
|---|---:|---|
| `GET /usuarios/<username>/avaliacoes` | Público | Retorna somente `nota_media` e `quantidade_avaliacoes`. |
| `GET /usuarios/<username>/avaliacoes/comentarios` | JWT | Apenas `papel == "admin"` ou empresa com assinatura ativa pode ver notas individuais e comentários. Empresa sem assinatura recebe `403`. |
| `POST /usuarios/<username>/avaliacoes` | JWT | Apenas empresa pode avaliar; o avaliador vem de `get_jwt_identity()`, nunca do JSON. |

O documento em `tbl_avaliacoes` deve guardar, no mínimo:

```json
{
  "avaliador_username": "empresa_x",
  "alvo_username": "influenciador_y",
  "nota": 5,
  "conteudo": "Ótima comunicação e bons resultados.",
  "criado_em": "data ISO 8601"
}
```

Não permitir autoavaliação. Também é recomendável limitar a uma avaliação por
empresa e influenciador e, futuramente, exigir que exista um chat entre ambos.
Os testes devem cobrir rating público, empresa assinante, empresa sem
assinatura, influenciador bloqueado, admin, autor forjado no JSON e
autoavaliação.

### Assinatura e pagamento — Paddle Sandbox

Provedor escolhido: **Paddle Billing, exclusivamente em sandbox**. O projeto é
acadêmico: usar somente credenciais e cartões de teste. Não há movimentação de
dinheiro real. A proposta anterior com Stripe foi substituída por Paddle.

#### Etapas 1 a 3 implementadas nesta branch

1. Documentação atualizada para o provedor escolhido e o escopo atual.
2. `POST /usuarios/me/assinatura/checkout` cria uma transação automática de
   sandbox vinculada ao usuário autenticado e salva o checkout pendente.
3. O botão **Ativar Premium** abre o Paddle.js em sandbox usando o ID da
   transação criada no servidor, com checkout em português e cartão de teste.

**Limite atual:** concluir o checkout não ativa nem renova o Premium local.
A confirmação automática, os webhooks e o cancelamento pelo aplicativo ainda
não foram implementados. Assinaturas criadas em testes devem ser canceladas
pelo painel de sandbox do Paddle enquanto a etapa 6 não estiver pronta.

#### Configuração local

No `.env` do backend, ignorado pelo Git:

```dotenv
PADDLE_API_KEY=chave_secreta_do_sandbox
PADDLE_CLIENT_TOKEN=token_publico_do_sandbox
PADDLE_PREMIUM_PRICE_ID=identificador_do_preco_mensal
```

O `.env.example` contém os identificadores públicos configurados, sem chave
secreta. Não colocar a API key no frontend nem em variáveis `VITE_*`.

- O backend aceita somente chave com prefixo `pdl_sdbx_apikey_` e usa a URL
  fixa `https://sandbox-api.paddle.com`. Não há opção de produção por ambiente.
- O frontend exige `environment: sandbox`, aceita somente token `test_` e
  chama `Paddle.Environment.set("sandbox")` antes de inicializar o SDK.
- No Paddle, o preço precisa estar ativo, em **BRL 2990 centavos**, com ciclo
  de **1 mês**, sem período de teste gratuito. O backend confere esses dados
  antes de criar a transação.
- Permissões da chave: `product.read`, `price.read`, `customer.write`,
  `transaction.write` e `subscription.write`. As três primeiras etapas usam
  principalmente preços e transações; clientes e assinaturas serão usados na
  continuidade da integração. `write` já inclui `read`.
- Configurar **Checkout > Checkout settings/configuration > Default payment
  link** no painel de sandbox, se ainda não existir. A orientação de testes
  publicada pelo Paddle sugere `https://localhost/`, mas esse valor foi recusado
  nesta conta. Portanto, falta configurar uma URL pública válida de uma página
  com Paddle.js. O arquivo `public/paddle-checkout.html` do frontend está pronto
  para essa finalidade, sem chave secreta ou acesso ao backend. Publicar a página
  depende de autorização do usuário. Enquanto o campo estiver ausente, o teste
  real de criação retorna o erro de configuração do Paddle; o código trata isso
  sem conceder Premium. O overlay pode continuar abrindo no frontend local.
- Reiniciar o backend após alterar o `.env`.

O SDK do navegador é carregado somente do CDN oficial:
`https://cdn.paddle.com/paddle/v2/paddle.js`.

#### Contrato do checkout implementado

| Operação | Regra |
|---|---|
| `POST /usuarios/me/assinatura/checkout` | JWT; empresa ou influenciador; corpo vazio ou `{}`. Usuário e preço vêm do token e do servidor. |
| `201` | Nova transação pendente criada. |
| `200` | Reutiliza transação pendente `draft` ou `ready` já salva para o usuário. |
| `400` | Corpo inválido ou tentativa de enviar preço, usuário ou validade. |
| `401` / `403` | Sem autenticação/conta válida ou perfil sem permissão. |
| `409` | Premium já ativo, checkout concorrente, transação já processada ou criação com resultado incerto aguardando conferência. |
| `502` / `503` | Falha no Paddle ou configuração inválida/ausente. Não libera acesso nem expõe credenciais. |

Resposta de sucesso:

```json
{
  "environment": "sandbox",
  "transaction_id": "txn_...",
  "client_token": "test_...",
  "reused": false
}
```

A coleção `tbl_checkout_pagamentos` usa o username como `_id` único, guarda a
referência local, o preço e o ID da transação. O servidor envia o username do
JWT e a referência em `custom_data` ao Paddle. Não aceita IDs de checkout do
cliente. Antes de reutilizar uma transação, consulta o Paddle e confere usuário,
preço, moeda e quantidade.

A reserva única impede POSTs concorrentes. Em erro HTTP definitivo, a reserva
é liberada. Em timeout/erro 5xx após o POST, o resultado pode ser incerto: marcar
`needs_review` e impedir novo checkout até conferência no sandbox. Em falha do
banco após a criação externa, a reserva também deve ser conferida antes de
permitir nova compra. Não apagar reservas incertas sem verificar a referência
em `custom_data` no Paddle. A reconciliação automática é trabalho futuro.

Com `PADDLE_API_KEY` configurada, a antiga rota
`PATCH /usuarios/<username>/assinatura` retorna `409`, impedindo ativação ou
cancelamento manual que desalinhe a conta local da assinatura externa. Sem
Paddle configurado, o fluxo legado de demonstração permanece disponível.

#### Etapas 4 a 7 pendentes

**4. Webhooks:** implementar `POST /webhooks/pagamentos`, configurar endereço
HTTPS acessível ao Paddle e guardar `PADDLE_WEBHOOK_SECRET` no `.env`. Validar
`Paddle-Signature` sobre o corpo original antes de processar o JSON. Não usar
JWT nessa rota; a autenticação é a assinatura do provedor.

**5. Sincronização do Premium:** consultar a transação/assinatura no Paddle,
validar produto, preço, referência e ambiente e atualizar o acesso somente após
pagamento confirmado. Tratar `transaction.completed`, falhas e eventos de
assinatura. Processar IDs de eventos uma única vez, com persistência consistente,
e suportar reenvios e eventos fora de ordem. Retorno do checkout ou evento do
navegador nunca são prova suficiente de pagamento. Migrar a validade atual de
`DD-MM-AAAA` para timestamps com fuso e usar o período pago fornecido pelo Paddle,
sem somar automaticamente 30 dias.

**6. Cancelamento:** implementar
`POST /usuarios/me/assinatura/cancelamento`, validar a titularidade e solicitar
cancelamento no fim do período pago. Manter Premium até essa data, sem novas
renovações. Exibir próxima renovação, falhas e cancelamento agendado na aba Planos.
Na primeira cobrança recusada, não ativar o plano; em falha de renovação, não
estender a validade e bloquear recursos Premium ao fim do período já pago.

**7. Testes completos:** cobrir aprovação, recusa, abandono, renovação mensal,
cancelamento, expiração, falsificação de preço/usuário, callbacks manuais,
webhooks inválidos/duplicados/fora de ordem e falhas no provedor e no banco.
Verificar empresas e influenciadores. Chat e contratos exigem assinatura ativa
do usuário que faz a ação; aplicar também no futuro endpoint de contratos.

#### Validação das etapas atuais

Bloqueio de configuração observado no sandbox real: Default payment link ainda
não configurado. Não considerar a abertura do checkout validada até configurar
esse campo e repetir o teste. Nenhum pagamento foi processado nesta etapa.


- Testes do backend exercitam o endpoint Flask e o cliente HTTP real contra um
  servidor local que simula os contratos do Paddle, incluindo falhas externas.
- Testes do frontend verificam o POST autenticado, rejeição de produção,
  abertura por `transactionId` e inicialização única do SDK em sandbox.
- O checkout pago, sua renovação e a liberação do Premium por webhook só poderão
  ser declarados concluídos depois das etapas pendentes.

Referências oficiais:

- [Autenticação](https://developer.paddle.com/api-reference/about/authentication/)
- [Criar uma transação](https://developer.paddle.com/api-reference/transactions/create-transaction/)
- [Abrir checkout por transação](https://developer.paddle.com/build/transactions/pass-transaction-checkout/)
- [Link de pagamento padrão](https://developer.paddle.com/build/transactions/default-payment-link/)
- [Sandbox](https://developer.paddle.com/sdks/sandbox/)
- [Configuração de checkout local do Paddle](https://github.com/PaddleHQ/paddle-agent-skills/blob/main/skills/checkout-web/SKILL.md)

## Análises de influenciadores para empresas

As análises devem ser um recurso separado do perfil público. A empresa que faz
a consulta define o nível de acesso; a assinatura do influenciador analisado
não interfere nessa decisão.

### Contrato de acesso

Uma rota inicial pode ser:

```text
GET /influenciadores/<username>/analise
```

Ela deve exigir JWT e consultar o usuário logado no banco. O acesso segue
estas regras:

- `papel == "admin"`: acesso completo para suporte e gestão.
- `tipo_perfil == "empresa"` com assinatura ativa: análise completa.
- `tipo_perfil == "empresa"` sem assinatura ativa: análise parcial.
- `tipo_perfil == "influenciador"`: `403`, pois influenciadores não usam essa
  rota para consultar análises de mercado.
- O usuário alvo precisa ter `tipo_perfil == "influenciador"`; caso contrário,
  retornar `404`.

O backend deve calcular `assinatura ativa` a partir de `assinatura` e
`expiracao_assinatura` persistidas no banco. Nunca receba o nível de acesso do
cliente nem confie apenas em uma claim antiga do token.

Uma resposta parcial deve conter apenas indicadores gerais, por exemplo:

```json
{
  "nivel_acesso": "parcial",
  "username": "ana-criadora",
  "seguidores": 120000,
  "taxa_engajamento": 4.2,
  "analise_completa_disponivel": true
}
```

A resposta completa mantém esses campos e acrescenta histórico de seguidores,
engajamento por plataforma, melhores horários e demografia. Manter o mesmo
formato-base evita que o frontend precise de duas telas diferentes.

### Caminho de implementação

1. Começar com uma coleção `analises_influenciadores`, separada de
   `tbl_testes`, preenchida manualmente ou por dados de demonstração. Cada
   documento deve ter `username`, `atualizado_em`, métricas gerais e métricas
   detalhadas.
2. Criar a rota acima e um serviço que transforma o mesmo documento em visão
   parcial ou completa conforme o acesso da empresa.
3. Registrar snapshots em uma coleção de métricas, com plataforma e data, para
   formar séries históricas sem sobrescrever dados antigos.
4. Quando houver dados reais, integrar apenas APIs oficiais das plataformas ou
   importações autorizadas pelo influenciador. Uma tarefa agendada coleta os
   dados, grava o snapshot e recalcula os indicadores derivados.

Os primeiros testes devem cobrir empresa sem assinatura, empresa assinante,
influenciador bloqueado, admin e alvo que não é influenciador.

## Configuração de deploy

Restringir o CORS à origem configurada do frontend e controlar `debug` por
variável de ambiente, deixando-o desligado em produção.
