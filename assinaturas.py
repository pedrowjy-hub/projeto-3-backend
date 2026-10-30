"""Estado local das assinaturas, atualizado somente por dados validados do sandbox."""
import hashlib
import hmac
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pymongo.errors import DuplicateKeyError
from pagamentos import ErroPagamento, configuracao_paddle, requisitar_paddle

UTC = timezone.utc
EVENTOS = {
    "transaction.completed", "transaction.payment_failed",
    "subscription.created", "subscription.activated", "subscription.updated",
    "subscription.canceled", "subscription.paused", "subscription.past_due", "subscription.resumed",
}
CAMPOS_LEGADOS = (
    "assinatura", "expiracao_assinatura", "premium_ate", "paddle_subscription_id",
    "paddle_customer_id", "paddle_status", "cancelamento_agendado",
    "renovacao_automatica", "proxima_renovacao", "falha_pagamento",
)


def dados_pagamento(usuario):
    """O objeto novo tem prioridade; campos antigos são apenas compatibilidade de leitura."""
    if "pagamento" in usuario:
        return usuario["pagamento"] if isinstance(usuario["pagamento"], dict) else {}
    return {
        "assinatura_id": usuario.get("paddle_subscription_id"),
        "cliente_id": usuario.get("paddle_customer_id"),
        "status": usuario.get("paddle_status"),
        "premium_ate": usuario.get("premium_ate"),
        "cancelamento_agendado": usuario.get("cancelamento_agendado", False),
        "proxima_renovacao": usuario.get("proxima_renovacao"),
    }


def campos_assinatura(usuario):
    """Contrato privado da API: indicadores derivados, sem duplicação no MongoDB."""
    pagamento = dados_pagamento(usuario)
    status = pagamento.get("status")
    cancelada = pagamento.get("cancelamento_agendado") is True
    renova = status in {"active", "past_due"} and not cancelada
    return {
        "assinatura": premium_ativo(usuario),
        "premium_ate": pagamento.get("premium_ate"),
        "paddle_subscription_id": pagamento.get("assinatura_id"),
        "paddle_status": status,
        "cancelamento_agendado": cancelada,
        "renovacao_automatica": renova,
        "proxima_renovacao": pagamento.get("proxima_renovacao") if renova else None,
        "falha_pagamento": status == "past_due",
    }


def data_utc(valor):
    if not isinstance(valor, str) or not re.search(r"(?:Z|[+-]\d{2}:\d{2})$", valor):
        return None
    try:
        data = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        return data.astimezone(UTC) if data.tzinfo else None
    except ValueError:
        return None


def iso(data):
    return data.astimezone(UTC).isoformat().replace("+00:00", "Z") if data else None


def premium_ativo(usuario, agora=None):
    agora = agora or datetime.now(UTC)
    if "pagamento" in usuario:
        pagamento = dados_pagamento(usuario)
        fim = data_utc(pagamento.get("premium_ate"))
        return bool(fim and fim > agora and pagamento.get("status") in {"active", "past_due", "canceled"})
    if usuario.get("assinatura") is not True:
        return False
    if "premium_ate" in usuario:
        fim = data_utc(usuario["premium_ate"])
        return fim is not None and fim > agora
    # Compatibilidade com contas antigas; novas assinaturas usam timestamps UTC.
    try:
        fim = datetime.strptime(usuario.get("expiracao_assinatura", ""), "%d-%m-%Y").date()
        return fim >= agora.astimezone(timezone(timedelta(hours=-3))).date()
    except (ValueError, TypeError):
        return False


def verificar_evento(corpo, cabecalho, agora=None):
    segredo = os.getenv("PADDLE_WEBHOOK_SECRET", "")
    if not segredo:
        raise ErroPagamento("Configure PADDLE_WEBHOOK_SECRET no backend.", 503)
    partes = {}
    for parte in (cabecalho or "").split(";"):
        chave, _, valor = parte.strip().partition("=")
        partes.setdefault(chave, []).append(valor)
    timestamps = partes.get("ts", [])
    try:
        if len(timestamps) != 1 or not timestamps[0].isdigit():
            raise ValueError
        timestamp = timestamps[0]
        if abs((time.time() if agora is None else agora) - int(timestamp)) > 5:
            raise ValueError
        esperado = hmac.new(segredo.encode(), timestamp.encode() + b":" + corpo, hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(esperado, assinatura) for assinatura in partes.get("h1", [])
                   if re.fullmatch(r"[a-fA-F0-9]{64}", assinatura)):
            raise ValueError
    except ValueError:
        raise ErroPagamento("Assinatura do webhook inválida ou expirada.", 401) from None
    try:
        evento = json.loads(corpo)
        if (not isinstance(evento, dict)
                or not re.fullmatch(r"evt_[a-z0-9]{26}", str(evento.get("event_id", "")))
                or not isinstance(evento.get("event_type"), str)
                or not isinstance(evento.get("data"), dict)):
            raise ValueError
        return evento
    except (ValueError, UnicodeDecodeError):
        raise ErroPagamento("Evento de webhook inválido.", 400) from None


def id_paddle(valor, prefixo):
    if not isinstance(valor, str) or not re.fullmatch(prefixo + r"_[a-z0-9]{26}", valor):
        raise ErroPagamento("Identificador de pagamento inválido.", 400)
    return valor


def validar_itens(entidade, preco):
    itens = entidade.get("items") or []
    if (entidade.get("currency_code") != "BRL" or entidade.get("collection_mode") != "automatic"
            or not isinstance(itens, list) or len(itens) != 1
            or not isinstance(itens[0], dict) or itens[0].get("quantity") != 1):
        raise ErroPagamento("Plano recebido não corresponde ao Premium configurado.", 400)
    item_preco = itens[0].get("price") or {}
    if (item_preco.get("id") != preco
            or item_preco.get("unit_price") != {"amount": "2990", "currency_code": "BRL"}
            or item_preco.get("billing_cycle") != {"interval": "month", "frequency": 1}):
        raise ErroPagamento("Preço recebido não corresponde ao Premium mensal.", 400)


def reservar_usuario(usuarios, username):
    agora = datetime.now(UTC)
    token = str(uuid4())
    anterior = usuarios.find_one_and_update({"username": username, "$or": [
        {"_paddle_lock_until": {"$exists": False}}, {"_paddle_lock_until": {"$lte": agora}},
    ]}, {"$set": {"_paddle_lock_until": agora + timedelta(seconds=60), "_paddle_lock_token": token}})
    if anterior is None:
        raise ErroPagamento("Assinatura em atualização. Tente novamente em instantes.", 503)
    return token


def liberar_usuario(usuarios, username, token):
    usuarios.update_one({"username": username, "_paddle_lock_token": token},
                        {"$unset": {"_paddle_lock_until": "", "_paddle_lock_token": ""}})


def localizar_usuario(db, assinatura, transacao):
    usuario = db["tbl_usuarios"].find_one({"pagamento.assinatura_id": assinatura["id"]})
    if not usuario:
        usuario = db["tbl_usuarios"].find_one({"pagamento": {"$exists": False}, "paddle_subscription_id": assinatura["id"]})
    if usuario:
        return usuario, transacao
    metadata = assinatura.get("custom_data") or (transacao or {}).get("custom_data") or {}
    username, referencia = metadata.get("username"), metadata.get("checkout_reference")
    if not isinstance(username, str) or not isinstance(referencia, str):
        return None, None
    checkout = db["tbl_checkout_pagamentos"].find_one({"_id": username, "reference": referencia})
    if not checkout or checkout.get("environment") != "sandbox":
        return None, None
    if transacao is None:
        transacao_id = checkout.get("transaction_id")
        if not transacao_id:
            raise ErroPagamento("Checkout ainda aguardando confirmação. Reenvie o evento.", 503)
        transacao = requisitar_paddle("GET", f"/transactions/{id_paddle(transacao_id, 'txn')}")
    dados = transacao.get("custom_data") or {}
    if (dados.get("username") != username or dados.get("checkout_reference") != referencia
            or transacao.get("subscription_id") != assinatura["id"]
            or checkout.get("transaction_id") not in (None, transacao["id"])):
        raise ErroPagamento("A assinatura não corresponde ao checkout autenticado.", 400)
    return db["tbl_usuarios"].find_one({"username": username, "papel": "usuario"}), transacao


def atualizar_estado(db, usuario, assinatura, transacao=None, lock_token=None):
    """Operação idempotente: usa datas absolutas, nunca acrescenta dias por evento."""
    _, _, preco = configuracao_paddle()
    validar_itens(assinatura, preco)
    pagamento = dados_pagamento(usuario)
    mesmo_plano = pagamento.get("assinatura_id") == assinatura["id"]
    id_paddle(assinatura.get("customer_id"), "ctm")
    if mesmo_plano and assinatura.get("customer_id") != pagamento.get("cliente_id"):
        raise ErroPagamento("Cliente da assinatura não corresponde ao usuário.", 400)
    pago_ate = data_utc(pagamento.get("premium_ate")) if mesmo_plano else None
    if transacao:
        validar_itens(transacao, preco)
        if (transacao.get("subscription_id") != assinatura["id"]
                or transacao.get("customer_id") != assinatura.get("customer_id")):
            raise ErroPagamento("Transação não corresponde à assinatura.", 400)
        if transacao.get("status") == "completed":
            periodo = transacao.get("billing_period") or {}
            inicio, fim = data_utc(periodo.get("starts_at")), data_utc(periodo.get("ends_at"))
            if not inicio or not fim or inicio >= fim:
                raise ErroPagamento("Período pago não foi confirmado pelo provedor.", 503)
            pago_ate = max(pago_ate, fim) if pago_ate else fim
    status = assinatura.get("status")
    if status not in {"active", "past_due", "canceled", "paused", "trialing"}:
        raise ErroPagamento("Status de assinatura não reconhecido.", 503)
    agendada = (assinatura.get("scheduled_change") or {}).get("action") == "cancel"
    valores = {
        "premium_ate": iso(pago_ate),
        "assinatura_id": assinatura["id"], "cliente_id": assinatura.get("customer_id"),
        "status": status, "cancelamento_agendado": agendada,
        "proxima_renovacao": assinatura.get("next_billed_at") if not agendada and status in {"active", "past_due"} else None,
    }
    filtro = {"username": usuario["username"]}
    if lock_token:
        filtro["_paddle_lock_token"] = lock_token
    resultado = db["tbl_usuarios"].update_one(filtro, {
        "$set": {"pagamento": valores}, "$unset": {campo: "" for campo in CAMPOS_LEGADOS},
    })
    if resultado.matched_count != 1:
        raise ErroPagamento("A reserva da assinatura expirou. Reenvie a atualização.", 503)
    return {**{chave: valor for chave, valor in usuario.items() if chave not in CAMPOS_LEGADOS}, "pagamento": valores}


def processar_evento(db, evento):
    configuracao_paddle()  # Credenciais live nunca podem atualizar o acesso.
    eventos = db["tbl_eventos_pagamentos"]
    anterior = eventos.find_one({"_id": evento["event_id"]})
    if anterior and anterior.get("processed"):
        return "duplicate"
    if not anterior:
        try:
            eventos.insert_one({"_id": evento["event_id"], "event_type": evento["event_type"], "processed": False})
        except DuplicateKeyError:
            # O processamento concorrente é serializado pela reserva do usuário.
            pass
    resultado = "ignored"
    if evento["event_type"] in EVENTOS:
        transacao = None
        if evento["event_type"].startswith("transaction."):
            transaction_id = id_paddle(evento["data"].get("id"), "txn")
            transacao = requisitar_paddle("GET", f"/transactions/{transaction_id}")
            if transacao.get("id") != transaction_id:
                raise ErroPagamento("Transação recebida não corresponde ao evento.", 400)
            if not transacao.get("subscription_id") and evento["event_type"] == "transaction.payment_failed":
                # Uma primeira tentativa recusada pode não criar assinatura no Paddle.
                eventos.update_one({"_id": evento["event_id"]}, {"$set": {"processed": True, "result": "ignored"}})
                return "ignored"
            sub_id = id_paddle(transacao.get("subscription_id"), "sub")
        else:
            sub_id = id_paddle(evento["data"].get("id"), "sub")
        assinatura = requisitar_paddle("GET", f"/subscriptions/{sub_id}")
        if assinatura.get("id") != sub_id:
            raise ErroPagamento("Assinatura recebida não corresponde ao evento.", 400)
        usuario, transacao = localizar_usuario(db, assinatura, transacao)
        if usuario:
            usuarios = db["tbl_usuarios"]
            lock = reservar_usuario(usuarios, usuario["username"])
            try:
                # Após obter a reserva, releia o estado local e o estado atual do provedor.
                atual = usuarios.find_one({"username": usuario["username"]})
                assinatura = requisitar_paddle("GET", f"/subscriptions/{sub_id}")
                if assinatura.get("id") != sub_id:
                    raise ErroPagamento("Resposta de assinatura inválida.", 400)
                if (dados_pagamento(atual).get("assinatura_id") not in (None, sub_id)
                        and premium_ativo(atual)):
                    referencia = (assinatura.get("custom_data") or {}).get("checkout_reference")
                    checkout_atual = db["tbl_checkout_pagamentos"].find_one({"_id": atual["username"], "reference": referencia})
                    if not checkout_atual:
                        # Uma notificação antiga não pode substituir uma nova assinatura paga.
                        eventos.update_one({"_id": evento["event_id"]}, {"$set": {"processed": True, "result": "ignored"}})
                        return "ignored"
                atualizar_estado(db, atual, assinatura, transacao, lock_token=lock)
                if transacao:
                    db["tbl_checkout_pagamentos"].update_one(
                        {"_id": usuario["username"], "reference": (transacao.get("custom_data") or {}).get("checkout_reference")},
                        {"$set": {"transaction_id": transacao["id"], "status": transacao["status"]}})
                resultado = "processed"
            finally:
                liberar_usuario(usuarios, usuario["username"], lock)
    eventos.update_one({"_id": evento["event_id"]}, {"$set": {"processed": True, "result": resultado}})
    return resultado


def cancelar_renovacao(db, username):
    configuracao_paddle()
    usuarios = db["tbl_usuarios"]
    usuario = usuarios.find_one({"username": username})
    if not usuario or usuario.get("papel") != "usuario":
        raise ErroPagamento("Usuário não encontrado.", 401)
    sub_id = dados_pagamento(usuario).get("assinatura_id")
    if not sub_id:
        raise ErroPagamento("Não há assinatura Paddle vinculada à sua conta.", 409)
    id_paddle(sub_id, "sub")
    lock = reservar_usuario(usuarios, username)
    try:
        usuario = usuarios.find_one({"username": username})
        assinatura = requisitar_paddle("GET", f"/subscriptions/{sub_id}")
        if assinatura.get("id") != sub_id or assinatura.get("customer_id") != dados_pagamento(usuario).get("cliente_id"):
            raise ErroPagamento("Não foi possível confirmar a titularidade da assinatura.", 403)
        validar_itens(assinatura, configuracao_paddle()[2])
        if assinatura.get("status") not in {"canceled", "paused"} and (assinatura.get("scheduled_change") or {}).get("action") != "cancel":
            assinatura = requisitar_paddle("POST", f"/subscriptions/{sub_id}/cancel", {"effective_from": "next_billing_period"})
        if assinatura.get("id") != sub_id or assinatura.get("customer_id") != dados_pagamento(usuario).get("cliente_id"):
            raise ErroPagamento("Resposta de cancelamento inválida.")
        if assinatura.get("status") != "canceled" and (assinatura.get("scheduled_change") or {}).get("action") != "cancel":
            raise ErroPagamento("O provedor ainda não confirmou o cancelamento.", 409)
        return atualizar_estado(db, usuario, assinatura, lock_token=lock)
    finally:
        liberar_usuario(usuarios, username, lock)
