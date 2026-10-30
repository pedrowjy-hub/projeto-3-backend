# projeto-3-backend

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

## Análises públicas do YouTube

O backend oferece dois endpoints autenticados para a primeira integração de
análise de influenciadores:

- `POST /analises/youtube` recebe `{"url": "https://www.youtube.com/@canal"}`,
  consulta os dados públicos do canal e até 10 vídeos recentes. Cada chamada
  salva ou atualiza a captura daquele dia no MongoDB, associada ao
  influenciador autenticado no sistema.
- `GET /analises/youtube` retorna até 90 capturas diárias do influenciador
  autenticado, em ordem cronológica, para alimentar gráficos.

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
