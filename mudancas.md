# Pendências do projeto

Este arquivo reúne apenas implementações e validações ainda pendentes.

## Avaliações de influenciadores

Adequar a rota de criação existente às regras abaixo e implementar as consultas
de nota pública e comentários detalhados. O alvo deve ser um perfil
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

## Interfaces de chat e fluxo de contratos

- Implementar a interface de mensagens integrada ao backend de chat existente.
- Implementar criação e fechamento de contratos, incluindo os endpoints e a
  interface. Validar participação e Premium ativo no backend em cada operação
  protegida, reutilizando `assinatura_premium_ativa`.
- Testar bloqueio de contas gratuitas ou expiradas e de usuários externos à
  conversa ou ao contrato, além do fluxo permitido para participantes Premium.

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
