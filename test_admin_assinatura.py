import pytest
from test_contas_ativas import context


@pytest.mark.parametrize("ativa", [True, False])
def test_salva_novo_pagamento(context, ativa):
    client, db, users, headers = context
    users["ana"].update({"assinatura": not ativa, "pagamento": {
        "status": "active", "premium_ate": "2098-01-01T00:00:00Z",
        "assinatura_id": "sub_preservada", "cliente_id": "ctm_preservado"}})
    db["tbl_usuarios"].update_one.return_value.matched_count = 1
    response = client.patch("/admin/usuarios/ana/assinatura", headers=headers("admin"), json={
        "ativa": ativa, "premium_ate": "2099-01-01T00:00:00Z" if ativa else None, "senha": "correta"})
    assert response.status_code == 200
    assert response.json["assinatura"] is ativa
    assert "pagamento" not in response.json and "senha" not in response.json
    query, change = db["tbl_usuarios"].update_one.call_args.args
    assert query["pagamento"] == users["ana"]["pagamento"]
    pagamento = change["$set"]["pagamento"]
    assert pagamento["assinatura_id"] == "sub_preservada"
    assert pagamento["cliente_id"] == "ctm_preservado"
    assert pagamento["status"] == ("active" if ativa else "paused")
    assert pagamento["premium_ate"] == ("2099-01-01T00:00:00Z" if ativa else None)
    assert "assinatura" in change["$unset"]


@pytest.mark.parametrize("actor,body,expected", [
    ("ana", {"ativa": True, "premium_ate": "2099-01-01T00:00:00Z", "senha": "correta"}, 403),
    ("admin", {"ativa": True, "premium_ate": "2099-01-01T00:00:00Z", "senha": "errada"}, 400),
    ("admin", {"ativa": True, "premium_ate": "2000-01-01T00:00:00Z", "senha": "correta"}, 400),
    ("admin", {"ativa": True, "premium_ate": "2099-01-01", "senha": "correta"}, 400),
    ("admin", {"ativa": "true", "premium_ate": None, "senha": "correta"}, 400),
    ("admin", {"ativa": False, "premium_ate": "2099-01-01T00:00:00Z", "senha": "correta"}, 400),
    ("admin", {"ativa": True, "premium_ate": None, "senha": "correta"}, 400),
])
def test_rejeita_sem_gravar(context, actor, body, expected):
    client, db, _, headers = context
    assert client.patch("/admin/usuarios/ana/assinatura", headers=headers(actor), json=body).status_code == expected
    db["tbl_usuarios"].update_one.assert_not_called()


def test_migra_legado(context):
    client, db, users, headers = context
    users["ana"].update({"assinatura": True, "expiracao_assinatura": "31-12-2099"})
    db["tbl_usuarios"].update_one.return_value.matched_count = 1
    response = client.patch("/admin/usuarios/ana/assinatura", headers=headers("admin"), json={
        "ativa": False, "premium_ate": None, "senha": "correta"})
    assert response.status_code == 200 and response.json["assinatura"] is False
    assert "expiracao_assinatura" not in response.json
    assert db["tbl_usuarios"].update_one.call_args.args[0]["pagamento"] == {"$exists": False}


def test_inexistente_e_concorrencia(context):
    client, db, _, headers = context
    body = {"ativa": True, "premium_ate": "2099-01-01T00:00:00Z", "senha": "correta"}
    assert client.patch("/admin/usuarios/ausente/assinatura", headers=headers("admin"), json=body).status_code == 404
    db["tbl_usuarios"].update_one.assert_not_called()
    db["tbl_usuarios"].update_one.return_value.matched_count = 0
    assert client.patch("/admin/usuarios/ana/assinatura", headers=headers("admin"), json=body).status_code == 409


@pytest.mark.parametrize("ativa", [True, False])
def test_estado_persistido_define_acesso_e_listagem(context, ativa):
    from assinaturas import premium_ativo
    from test_assinaturas import Collection
    client, db, users, headers = context
    collection = Collection(*users.values())
    db["tbl_usuarios"] = collection
    response = client.patch("/admin/usuarios/ana/assinatura", headers=headers("admin"), json={
        "ativa": ativa, "premium_ate": "2099-01-01T00:00:00Z" if ativa else None, "senha": "correta"})
    assert response.status_code == 200
    stored = collection.find_one({"username": "ana"})
    assert premium_ativo(stored) is ativa
    assert "assinatura" not in stored and "expiracao_assinatura" not in stored
    reloaded = client.get("/admin/usuario/ana", headers=headers("admin"))
    assert reloaded.status_code == 200 and reloaded.json["assinatura"] is ativa
    assert "pagamento" not in reloaded.json and "senha" not in reloaded.json


def test_sessao_obrigatoria_e_falha_de_gravacao(context):
    from pymongo.errors import PyMongoError
    client, db, _, headers = context
    body = {"ativa": False, "premium_ate": None, "senha": "correta"}
    assert client.patch("/admin/usuarios/ana/assinatura", json=body).status_code == 401
    db["tbl_usuarios"].update_one.assert_not_called()
    db["tbl_usuarios"].update_one.side_effect = PyMongoError("falha simulada")
    assert client.patch("/admin/usuarios/ana/assinatura", headers=headers("admin"), json=body).status_code == 503
