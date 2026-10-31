# Pendências do projeto

Este arquivo reúne apenas implementações e validações ainda pendentes.

## Validação externa dos pagamentos — Paddle Sandbox

- Executar um checkout fictício de ponta a ponta usando os túneis e o destino
  de notificações já configurados. Confirmar a entrega do webhook, a atualização
  de `pagamento` no MongoDB e a liberação do Premium na interface.
- Testar renovação, falha de pagamento e cancelamento no sandbox, conferindo o
  período de acesso e o estado exibido na aba Planos após cada cenário.
- Verificar que notificações repetidas não duplicam a atualização e que uma
  falha de entrega pode ser reenviada sem estender indevidamente a validade.
- Conferir, nesse fluxo externo, os selos de plano e o popup de boas-vindas:
  somente após confirmação do Premium e sem reaparecer a cada renovação.

Usar exclusivamente sandbox e cartões fictícios. Os testes automatizados locais
já passaram; falta validar o fluxo completo entre navegador, Paddle e MongoDB.
Se os túneis forem recriados com outros endereços, atualizar o Default payment
link do frontend e a URL `/webhooks/pagamentos` do backend no Paddle.

## Análises de influenciadores para empresas

As rotas `/analises/youtube` e `/analises/youtube/<username>` já permitem
capturar e consultar o histórico público do YouTube. Falta aplicar os níveis
parcial e completo por plano descritos abaixo; atualmente empresas consultam
esse histórico sem exigir Premium. Reutilizar a integração e o histórico
existentes ao implementar essa diferenciação.

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

O backend deve calcular `assinatura ativa` a partir de `pagamento.status` e
`pagamento.premium_ate` persistidos no banco, usando `assinatura_premium_ativa`.
Os campos antigos são apenas compatibilidade de leitura. Nunca receba o nível de acesso do
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

## Homologação externa dos contratos

- Reiniciar o backend após atualizar o `.env` e confirmar que o processo em execução reconhece o Client ID. A API App, o Client ID, o domínio do frontend e o callback já foram configurados e conferidos por consulta à API.
- Validar com duas contas Premium e e-mails próprios/autorizados: negociar pelo chat, publicar PDF, ambos revisarem/aprovarem a mesma versão, preparar a assinatura, assinar dentro do site pelos dois e baixar o PDF final. Manter exclusivamente modo de teste.
- Confirmar recebimento dos eventos, recusa e entrega repetida no serviço real.
- Evoluções futuras: paginação do histórico, cancelamento/expiração de solicitações na interface e tratamento completo de cancelamentos feitos no painel externo.

Chat, vínculo obrigatório à conversa, aprovação por participante e assinatura incorporada já estão implementados localmente. Detalhes e limites em `CONTRATOS.md`. Falta concluir a homologação externa no navegador e confirmar a entrega dos eventos.

## Sessões independentes por aba

Atualmente o token `access_token` fica no `localStorage`, compartilhado entre
abas e janelas da mesma origem no mesmo perfil de navegador. Entrar em outra
conta substitui a sessão usada pelas demais abas.

- Centralizar o acesso ao token em um serviço de sessão e armazená-lo no
  `sessionStorage`, atualizando login, logout, chamadas autenticadas e validações
  de sessão em todas as telas e componentes.
- Deixar de usar o token antigo do `localStorage`, sem migrá-lo automaticamente
  para todas as abas. Solicitar novo login quando necessário.
- Ajustar o provedor de aparência para identificar a conta pela sessão da aba.
  Manter as preferências e seus caches vinculados ao usuário; apenas a sessão
  de autenticação deve ser isolada por aba.
- Garantir que respostas e operações pendentes da conta anterior não atualizem
  a interface após troca de conta ou logout na mesma aba.
- Testar empresa e influenciador simultaneamente: login e logout em uma aba não
  devem trocar ou encerrar a conta da outra; mensagens, contratos e preferências
  devem usar a identidade correta. Recarregar a aba deve preservar sua sessão.
- Conferir também abas duplicadas: o navegador pode copiar inicialmente o
  `sessionStorage`, mas logins posteriores devem permanecer independentes.

Até essa implementação, testar duas contas usando perfis de navegador separados
ou uma janela normal e outra anônima. Essa alteração ainda não foi implementada.

## Configuração de deploy

- Restringir o CORS à origem configurada do frontend.
- Controlar `debug` por variável de ambiente, deixando-o desligado em produção.
- Fazer os deploys do frontend e do backend e configurar o encaminhamento de
  `/api` para o backend; o proxy do Vite atende apenas ao desenvolvimento.
- Substituir os endereços temporários no Paddle pelas URLs HTTPS dos deploys:
  Default payment link apontando para `/paddle-checkout.html` do frontend e
  notificações apontando para `/webhooks/pagamentos` do backend.
- Repetir o teste completo de sandbox nos endereços definitivos, mantendo as
  credenciais de teste no backend e fora do repositório.
