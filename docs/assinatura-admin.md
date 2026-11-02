# Ajuste administrativo de acesso Premium

`PATCH /admin/usuarios/{username}/assinatura` exige Bearer JWT de administrador ativo e confirmação da senha desse administrador.

Corpo exato: `{"ativa": true, "premium_ate": "2099-01-01T00:00:00Z", "senha": "<senha atual>"}`.
Para desativar: `{"ativa": false, "premium_ate": null, "senha": "<senha atual>"}`.

A ativação exige data futura com fuso horário. O frontend usa horário local e envia UTC. O servidor grava `pagamento.status` como `active` ou `paused` e `pagamento.premium_ate`, preserva os demais dados de pagamento e remove os campos legados. A resposta 200 contém o usuário atualizado no formato da listagem administrativa, com `assinatura` calculada, sem senha nem objeto interno `pagamento`.

O ajuste muda somente o acesso local: não gera pagamento, não cancela cobranças nem altera a assinatura no Paddle. A próxima atualização validada do provedor pode substituí-lo.

Erros: 400 para corpo, validade ou senha inválidos; 401 para sessão inválida/conta desativada; 403 para usuário sem papel admin; 404 para usuário inexistente; 409 para atualização concorrente (recarregar antes de tentar novamente); 503 para falha de gravação.
