# Contratos vinculados ao chat — Dropbox Sign em teste

## Fluxo implementado

1. Empresa e influenciador com Premium ativo iniciam uma negociação em `/mensagens`.
2. Dentro da conversa, qualquer participante publica um PDF de até 5 MB para revisão.
3. Cada um baixa/revisa e aprova a mesma versão. A aprovação guarda identidade, SHA-256 e data; uma mensagem como "aceito" não aprova nem assina o documento.
4. Após as duas aprovações, qualquer participante prepara a assinatura. O backend cria uma solicitação incorporada no Dropbox Sign com `test_mode=1` fixo.
5. Cada participante usa **Assinar aqui** na conversa. O backend autentica a pessoa e gera acesso temporário exclusivamente à assinatura dela. A janela do Dropbox Sign abre dentro do site.
6. O backend confirma as duas assinaturas pela API/webhook antes de liberar o PDF final. O evento JavaScript do navegador não conclui o contrato.

A aba `/contratos` reúne os documentos e permite abrir a negociação correspondente. Criação, aprovação e assinatura acontecem na conversa. Registros antigos sem chat permanecem consultáveis; é necessário criar uma nova versão vinculada à conversa para assinar dentro da plataforma.

## Configuração que ainda depende do painel

No `.env` local do backend, fora do Git:

- `DROPBOX_SIGN_API_KEY`: chave privada, usada somente no servidor.
- `DROPBOX_SIGN_CLIENT_ID`: Client ID de uma **API App**, diferente da API Key. Configurado nesta instalação e validado por consulta à API, junto com o domínio e o callback da App.

No Dropbox Sign, abrir **Settings → API → API apps** e criar/configurar uma API App. Copiar seu Client ID para a variável acima. Definir o domínio do frontend na configuração da App e o callback da própria App para a URL HTTPS pública do backend terminando em `/webhooks/assinaturas`. Reiniciar o backend após alterar o `.env`.

O SDK usa `skipDomainVerification` exclusivamente com solicitações de teste. Para homologação no navegador, preferir os túneis HTTPS do frontend e backend. Se o túnel mudar, atualizar a configuração correspondente. Não habilitar produção. Sem Client ID, revisão e chat funcionam, mas preparar assinatura retorna erro de configuração sem enviar convites por e-mail como alternativa.

O host da API é fixo em `https://api.hellosign.com/v3`. Todo envio usa `test_mode=1` e respostas fora desse modo são rejeitadas. A API Key do serviço também pode permitir produção: o bloqueio desta integração vem do modo de teste fixo no código. Documentos de demonstração não devem ser tratados como contratos válidos de produção.

## Contrato da API

Todas as rotas abaixo, exceto webhook, exigem JWT e Premium ativo. Identidades e participantes são determinados pelo servidor. Criar conversa, publicar PDF, preparar assinatura e abrir assinatura validam os dois participantes ativos e Premium. Leitura e envio de mensagens exigem Premium do usuário autenticado.

| Método e rota | Entrada e comportamento |
| --- | --- |
| `GET /conversas` | Lista até 100 negociações do usuário. |
| `POST /conversas` | JSON `{parceiro}`; deriva empresa/influenciador e reutiliza conversa existente do par. |
| `GET /conversas/<chat_id>/mensagens` | Últimas 200 mensagens; histórico anterior permanece no banco. |
| `POST /conversas/<chat_id>/mensagens` | JSON `{mensagem}`, até 4.000 caracteres; remetente vem do JWT. |
| `GET /contratos?chat_id=...` | Até 50 contratos próprios, opcionalmente filtrados pela conversa autorizada. |
| `POST /contratos` | Multipart `titulo` (3–120), `chat_id`, `documento` (PDF até 5 MB). Cria rascunho sem chamar o provedor. |
| `POST /contratos/<id>/aprovar` | JSON `{sha256}`; registra apenas a aprovação do participante autenticado para a versão imutável. |
| `POST /contratos/<id>/enviar` | Corpo vazio ou `{}`; exige ambas as aprovações, revalida e-mails/Premium e prepara assinatura incorporada. |
| `POST /contratos/<id>/assinar` | Corpo vazio ou `{}`; retorna acesso temporário do próprio signatário e Client ID público, com `Cache-Control: private, no-store`. |
| `POST /contratos/<id>/sincronizar` | Consulta o provedor e atualiza o estado validado. |
| `GET /contratos/<id>/documento?tipo=original` | Download privado do PDF original. |
| `GET /contratos/<id>/documento?tipo=assinado` | Download privado após confirmação das duas assinaturas; `409` enquanto PDF estiver em preparação. |
| `POST /webhooks/assinaturas` | Multipart `json`, sem JWT; HMAC obrigatório, resposta `Hello API Event Received`. |

Erros: entrada inválida `400`, autenticação ausente `401`, Premium ausente `403`, recurso inacessível `404`, versão/estado pendente `409`, falha do provedor/banco/configuração `502/503`. Não são aceitos e-mails, remetente, signatário, estado ou modo de produção enviados pelo cliente.

## Persistência, segurança e limites

`tbl_chats` preserva participantes e mensagens. `tbl_contratos` guarda UUID, chat, par fixo, participantes, e-mails capturados na revisão, PDF original imutável, SHA-256, aprovações por papel, datas, status e referência privada do provedor. Alterações de PDF ou participantes exigem nova versão. E-mail alterado depois da revisão bloqueia o preparo.

Estados: `rascunho`, `enviando`, `aguardando_assinaturas`, `parcialmente_assinado`, `assinado`, `recusado`, `falha`, `revisao_necessaria`. Reserva atômica evita envio simultâneo duplicado. Resposta perdida ou inválida não permite reenvio automático; é preciso conferir no painel. Um webhook validado pode recuperar a referência persistida.

O webhook valida HMAC SHA-256 de `event_time + event_type`, depois consulta a API e confere metadados, modo de teste, ID e ambos os e-mails. Eventos repetidos são idempotentes; consultas antigas não reabrem contrato concluído. A URL temporária de assinatura não é persistida nem enviada para o outro participante. O Client ID é público, a API Key não sai do backend.

O chat e os contratos consultam alterações pelo servidor periodicamente enquanto a página está visível. Ainda não há paginação para mensagens anteriores às últimas 200 nem contratos anteriores aos últimos 50. Cancelamento/expiração de solicitações pela interface e tratamento completo de cancelamentos feitos no painel são evoluções futuras. Não são emitidos novos acessos de assinatura sem Premium, mas a integração não cancela automaticamente uma solicitação já criada se o plano expirar.

## Verificação e pendências

Testes usam Flask e HTTP reais contra um servidor local que simula Dropbox Sign, com documentos/e-mails fictícios. Não contatam o provedor nem disparam solicitações externas. Cobrem vínculo ao chat, isolamento entre participantes, aprovação dos dois, hash, concorrência/reenvio, identidade do signatário, webhook e PDF. No frontend, testes verificam o contrato dos serviços e a abertura/encerramento do SDK em modo de teste. Build/lint verificam a integração do aplicativo.

A autenticação da API Key foi validada anteriormente por GET de conta, sem divulgar seu conteúdo. O CLI Postman não inicia neste computador devido à política gerenciada Private Cloud; a validação local usa pytest e servidor HTTP local.

Pendente: confirmar que o backend em execução carregou o `.env` atualizado e concluir a homologação no navegador com duas contas Premium e e-mails próprios/autorizados: negociar, publicar/revisar/aprovar PDF, preparar, assinar pelos dois, receber eventos e baixar PDF final. Conferir também recusa e entrega repetida. Os testes locais não substituem essa homologação externa. Para testar contas simultaneamente antes do isolamento de sessões por aba, usar perfis de navegador separados ou uma janela normal e outra anônima.

## Referências oficiais

- [Solicitação incorporada](https://developers.hellosign.com/api/signature-request/create-embedded)
- [URL do próprio signatário](https://developers.hellosign.com/api/embedded/sign-url)
- [SDK e assinatura incorporada](https://developers.hellosign.com/docs/walkthroughs/embedded-signing)
- [Consulta de status](https://developers.hellosign.com/api/signature-request/get)
- [Download PDF](https://developers.hellosign.com/api/signature-request/files)
- [Eventos e callbacks](https://developers.hellosign.com/docs/guides/events-and-callbacks/walkthrough)

Solicitação incorporada e URL de assinatura também foram conferidas via Orbit; os campos usados seguem o multipart oficial.
