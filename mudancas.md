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

### Assinatura e pagamento

Substituir a atual rota de simulação por uma integração com um provedor de
pagamentos externo. O cliente nunca pode definir diretamente `assinatura` ou
`expiracao_assinatura`; somente o backend, após confirmar o evento do provedor,
atualiza esses campos.

Fluxo:

```text
empresa autenticada → backend cria checkout no provedor → frontend redireciona
→ pagamento/assinatura confirmado → webhook validado → backend atualiza plano
```

Rotas necessárias:

| Rota | Regra |
|---|---|
| `POST /usuarios/me/assinatura/checkout` | Requer JWT; cria a cobrança usando a identidade do token e devolve a URL do checkout. |
| `POST /usuarios/me/assinatura/cancelamento` | Requer JWT; solicita o cancelamento do plano do usuário logado ao provedor. |
| `POST /webhooks/pagamentos` | Pública para o provedor; valida a assinatura do evento e é a única que ativa, renova ou define a expiração. |

No checkout, registrar no banco um pagamento pendente associado ao usuário
logado e ao identificador externo da cobrança. No webhook:

1. Validar a assinatura enviada pelo provedor.
2. Consultar o pagamento ou assinatura na API do provedor, em vez de confiar
   apenas no corpo recebido.
3. Confirmar que o status é aprovado/ativo e que a referência pertence ao
   usuário esperado.
4. Processar cada identificador de evento uma única vez, para evitar renovações
   duplicadas quando o webhook for reenviado.

As chaves da API e do webhook ficam apenas em variáveis de ambiente do backend.
Para cobrança recorrente, usar o recurso de assinaturas do provedor escolhido;
para pagamento único, criar um checkout por período de acesso.

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
