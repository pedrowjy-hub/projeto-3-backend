"""Contratos PDF privados entre empresa e influenciador; assinatura externa em teste."""
import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from io import BytesIO
from uuid import UUID, uuid4

from bson import Binary
from flask import Blueprint, jsonify, request, send_file
from flask_jwt_extended import get_jwt_identity, jwt_required
from pymongo.errors import PyMongoError
from werkzeug.utils import secure_filename

from assinaturas import premium_ativo
from dropbox_sign import ErroAssinatura, chave_api, client_id, identificador, enviar_pedido, consultar_pedido, baixar_pdf, url_assinatura
from conversas import acessar_conversa

MAX_PDF = 5 * 1024 * 1024
EVENTOS_ASSINATURA = {"signature_request_sent", "signature_request_signed", "signature_request_all_signed",
    "signature_request_declined", "signature_request_downloadable", "signature_request_invalid", "signature_request_email_bounce"}
PUBLICOS = {"_id": 1, "titulo": 1, "participantes": 1, "criador": 1, "signatarios": 1,
    "status": 1, "criado_em": 1, "atualizado_em": 1, "nome_arquivo": 1, "sha256": 1, "test_mode": 1,
    "chat_id": 1, "conversa": 1, "aprovacoes": 1, "modo_assinatura": 1}


def agora():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def serializar(contrato):
    result = {key: contrato.get(key) for key in PUBLICOS if key != "_id"}
    result["id"] = contrato["_id"]
    return result


def validar_pedido(contrato, pedido):
    if pedido.get("test_mode") is not True:
        raise ErroAssinatura("Assinatura fora do modo de teste foi bloqueada.", 409)
    identificador(pedido.get("signature_request_id"))
    if contrato.get("_dropbox_id") not in (None, pedido["signature_request_id"]):
        raise ErroAssinatura("Assinatura não corresponde ao contrato.")
    metadata = pedido.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ErroAssinatura("Metadados da assinatura inválidos.")
    if any(metadata.get(key) != value for key, value in {
        "contrato_id": contrato["_id"], "envio_ref": contrato.get("_envio_ref"), "sha256": contrato["sha256"]}.items()):
        raise ErroAssinatura("O vínculo entre contrato e assinatura não foi confirmado.")
    signatures = pedido.get("signatures")
    expected = {pessoa["email"].casefold() for pessoa in contrato["signatarios"]}
    if not isinstance(signatures, list) or len(signatures) != 2 or any(not isinstance(item, dict) for item in signatures):
        raise ErroAssinatura("Signatários não confirmados pelo provedor.")
    if {str(item.get("signer_email_address", "")).casefold() for item in signatures} != expected:
        raise ErroAssinatura("Os signatários recebidos não correspondem aos participantes.")
    return signatures


def atualizar(colecao, contrato, pedido):
    signatures = validar_pedido(contrato, pedido)
    signed = all(item.get("status_code") == "signed" for item in signatures)
    if pedido.get("is_complete") is True and signed:
        status = "assinado"
    elif pedido.get("is_declined") is True:
        status = "recusado"
    elif pedido.get("has_error") is True:
        status = "falha"
    elif any(item.get("status_code") == "signed" for item in signatures):
        status = "parcialmente_assinado"
    else:
        status = "aguardando_assinaturas"
    people = []
    for pessoa in contrato["signatarios"]:
        signature = next(item for item in signatures if item["signer_email_address"].casefold() == pessoa["email"].casefold())
        people.append({**pessoa, "assinado": signature.get("status_code") == "signed"})
    values = {"status": status, "signatarios": people, "_dropbox_id": pedido["signature_request_id"], "atualizado_em": agora()}
    filtro = {"_id": contrato["_id"], "_envio_ref": contrato["_envio_ref"]}
    # Uma consulta antiga não reabre um contrato já assinado/recusado.
    if status != "assinado":
        filtro["status"] = {"$nin": ["assinado", "recusado"]}
    colecao.update_one(filtro, {"$set": values})
    return colecao.find_one({"_id": contrato["_id"]})


def registrar_contratos(app, conectar_db):
    bp = Blueprint("contratos", __name__)

    @bp.errorhandler(ErroAssinatura)
    def erro_assinatura(erro):
        return jsonify({"erro": str(erro)}), erro.status

    @bp.errorhandler(PyMongoError)
    def erro_banco(_erro):
        return jsonify({"erro": "Não foi possível salvar ou consultar o contrato. Tente novamente."}), 503

    def usuario_premium(username):
        usuario = conectar_db()["tbl_usuarios"].find_one({"username": username, "papel": "usuario", "ativo": {"$ne": False}})
        if not usuario:
            raise ErroAssinatura("Participante não encontrado ou desativado.", 404)
        if not premium_ativo(usuario):
            raise ErroAssinatura("Os participantes precisam de Premium ativo para iniciar uma assinatura.", 403)
        return usuario

    def contrato_do_usuario(contrato_id):
        usuario_premium(get_jwt_identity())
        try:
            if str(UUID(contrato_id)) != contrato_id:
                raise ValueError
        except ValueError:
            raise ErroAssinatura("Contrato não encontrado.", 404) from None
        contrato = conectar_db()["tbl_contratos"].find_one({"_id": contrato_id, "participantes": get_jwt_identity()})
        if not contrato:
            raise ErroAssinatura("Contrato não encontrado.", 404)
        return contrato

    def sincronizar(contrato):
        if not contrato.get("_dropbox_id"):
            raise ErroAssinatura("O envio ainda não tem confirmação. Não reenvie; confira a solicitação no provedor.", 409)
        return atualizar(conectar_db()["tbl_contratos"], contrato, consultar_pedido(contrato["_dropbox_id"]))

    def vinculo_conversa(contrato):
        chat, _ = acessar_conversa(conectar_db(), contrato.get("chat_id"), get_jwt_identity())
        pair = {key: chat[key] for key in ("id_empresa", "id_influenciador")}
        if contrato.get("conversa") != pair or set(contrato["participantes"]) != set(pair.values()):
            raise ErroAssinatura("O contrato não corresponde a esta negociação.", 409)
        return chat

    def aprovados(contrato):
        pair = contrato.get("conversa") or {}
        approvals = contrato.get("aprovacoes") or {}
        return all(isinstance(approvals.get(role), dict)
            and approvals[role].get("username") == pair.get("id_" + role)
            and approvals[role].get("sha256") == contrato["sha256"]
            for role in ("empresa", "influenciador"))

    @bp.route("/contratos", methods=["GET", "POST"])
    @jwt_required()
    def contratos():
        usuario = usuario_premium(get_jwt_identity())
        colecao = conectar_db()["tbl_contratos"]
        if request.method == "GET":
            filtro = {"participantes": usuario["username"]}
            chat_id = request.args.get("chat_id")
            if chat_id:
                acessar_conversa(conectar_db(), chat_id, usuario["username"])
                filtro["chat_id"] = chat_id
            registros = colecao.find(filtro, PUBLICOS).sort("criado_em", -1).limit(50)
            return jsonify([serializar(item) for item in registros])
        if set(request.form) != {"titulo", "chat_id"} or set(request.files) != {"documento"} or len(request.files.getlist("documento")) != 1:
            raise ErroAssinatura("Crie o contrato dentro de uma conversa, com título e um único PDF.", 400)
        titulo = request.form["titulo"].strip()
        if not 3 <= len(titulo) <= 120:
            raise ErroAssinatura("O título deve ter entre 3 e 120 caracteres.", 400)
        chat, _ = acessar_conversa(conectar_db(), request.form["chat_id"], usuario["username"])
        pair = {key: chat[key] for key in ("id_empresa", "id_influenciador")}
        outro = chat["id_empresa"] if usuario["username"] == chat["id_influenciador"] else chat["id_influenciador"]
        parceiro = usuario_premium(outro)
        if {usuario.get("tipo_perfil"), parceiro.get("tipo_perfil")} != {"empresa", "influenciador"}:
            raise ErroAssinatura("Escolha uma parceria entre empresa e influenciador.", 400)
        people = []
        for pessoa in (usuario, parceiro):
            email = pessoa.get("email", "")
            email = email.strip() if isinstance(email, str) else ""
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                raise ErroAssinatura("Os dois participantes precisam de e-mail válido no perfil.", 400)
            people.append({"username": pessoa["username"], "nome": pessoa.get("nome") or pessoa["username"], "email": email, "assinado": False})
        if people[0]["email"].casefold() == people[1]["email"].casefold():
            raise ErroAssinatura("Os participantes precisam de e-mails distintos.", 400)
        arquivo = request.files["documento"]
        pdf = arquivo.read(MAX_PDF + 1)
        if not pdf.startswith(b"%PDF-") or len(pdf) > MAX_PDF:
            raise ErroAssinatura("Envie um PDF válido de até 5 MB.", 400)
        contrato = {"_id": str(uuid4()), "titulo": titulo, "criador": usuario["username"],
            "participantes": [usuario["username"], parceiro["username"]], "signatarios": people,
            "status": "rascunho", "test_mode": True, "criado_em": agora(), "atualizado_em": agora(),
            "chat_id": str(chat["_id"]), "conversa": pair, "aprovacoes": {}, "modo_assinatura": "incorporada",
            "_pdf": Binary(pdf), "nome_arquivo": (secure_filename(arquivo.filename or "contrato.pdf") or "contrato.pdf")[:120], "sha256": hashlib.sha256(pdf).hexdigest()}
        colecao.insert_one(contrato)
        return jsonify(serializar(contrato)), 201

    @bp.route("/contratos/<contrato_id>/aprovar", methods=["POST"])
    @jwt_required()
    def aprovar(contrato_id):
        contrato = contrato_do_usuario(contrato_id)
        chat = vinculo_conversa(contrato)
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {"sha256"} or body["sha256"] != contrato["sha256"]:
            raise ErroAssinatura("O documento mudou ou a versão não foi confirmada. Revise o PDF novamente.", 409)
        role = "empresa" if chat["id_empresa"] == get_jwt_identity() else "influenciador"
        if contrato["status"] != "rascunho":
            raise ErroAssinatura("A revisão desta versão já foi encerrada.", 409)
        value = {"username": get_jwt_identity(), "sha256": contrato["sha256"], "aprovado_em": agora()}
        colecao = conectar_db()["tbl_contratos"]
        result = colecao.update_one({"_id": contrato_id, "status": "rascunho", "sha256": body["sha256"]},
            {"$set": {f"aprovacoes.{role}": value, "atualizado_em": agora()}})
        if result.matched_count != 1:
            raise ErroAssinatura("A versão não está mais disponível para revisão.", 409)
        return jsonify(serializar(colecao.find_one({"_id": contrato_id})))

    @bp.route("/contratos/<contrato_id>/enviar", methods=["POST"])
    @jwt_required()
    def enviar(contrato_id):
        if request.data and request.get_json(silent=True) != {}:
            raise ErroAssinatura("O envio utiliza somente os dados já revisados do contrato.", 400)
        contrato = contrato_do_usuario(contrato_id)
        vinculo_conversa(contrato)
        if contrato.get("modo_assinatura") != "incorporada":
            raise ErroAssinatura("Crie uma nova versão dentro da conversa para assinar na plataforma.", 409)
        if contrato.get("_dropbox_id"):
            return jsonify(serializar(sincronizar(contrato)))
        chave_api()
        configuracao_client = client_id()
        if not aprovados(contrato):
            raise ErroAssinatura("Os dois participantes precisam revisar e aprovar o mesmo PDF antes da assinatura.", 409)
        for pessoa in contrato["signatarios"]:
            atual = usuario_premium(pessoa["username"])
            if str(atual.get("email", "")).strip().casefold() != pessoa["email"].casefold():
                raise ErroAssinatura("Um e-mail foi alterado. Crie uma nova versão e confira os destinatários.", 409)
        colecao = conectar_db()["tbl_contratos"]
        ref = str(uuid4())
        reservado = colecao.update_one({"_id": contrato_id, "status": "rascunho"}, {"$set": {"status": "enviando", "_envio_ref": ref, "_client_id": configuracao_client}})
        if reservado.matched_count != 1:
            raise ErroAssinatura("Envio em andamento ou aguardando conferência. Não envie outra cópia.", 409)
        contrato = {**contrato, "status": "enviando", "_envio_ref": ref, "_client_id": configuracao_client}
        try:
            pedido = enviar_pedido(contrato)
            validar_pedido(contrato, pedido)
            # Persista o vínculo antes de atualizar os demais campos.
            colecao.update_one({"_id": contrato_id, "_envio_ref": ref}, {"$set": {"_dropbox_id": pedido["signature_request_id"]}})
            return jsonify(serializar(atualizar(colecao, contrato, pedido)))
        except ErroAssinatura as erro:
            colecao.update_one({"_id": contrato_id, "_envio_ref": ref, "status": "enviando"},
                {"$set": {"status": "revisao_necessaria"}})
            raise erro

    @bp.route("/contratos/<contrato_id>/assinar", methods=["POST"])
    @jwt_required()
    def assinar(contrato_id):
        if request.data and request.get_json(silent=True) != {}:
            raise ErroAssinatura("A assinatura usa somente a identidade autenticada.", 400)
        contrato = contrato_do_usuario(contrato_id)
        vinculo_conversa(contrato)
        if contrato.get("modo_assinatura") != "incorporada" or not aprovados(contrato) or not contrato.get("_client_id"):
            raise ErroAssinatura("A assinatura precisa ser preparada após a aprovação dos dois participantes.", 409)
        for pessoa in contrato["signatarios"]:
            usuario_premium(pessoa["username"])
        if not contrato.get("_dropbox_id"):
            raise ErroAssinatura("O contrato ainda não foi preparado para assinatura.", 409)
        pedido = consultar_pedido(contrato["_dropbox_id"])
        signatures = validar_pedido(contrato, pedido)
        if pedido.get("is_declined") or pedido.get("has_error"):
            raise ErroAssinatura("Este contrato não está disponível para assinatura.", 409)
        email = next(pessoa["email"] for pessoa in contrato["signatarios"] if pessoa["username"] == get_jwt_identity())
        signature = next(item for item in signatures if item["signer_email_address"].casefold() == email.casefold())
        if signature.get("status_code") != "awaiting_signature":
            raise ErroAssinatura("Sua assinatura já foi concluída ou não está disponível.", 409)
        data = url_assinatura(signature.get("signature_id"))
        return jsonify({**data, "client_id": contrato["_client_id"], "test_mode": True}), 200, {"Cache-Control": "private, no-store"}

    @bp.route("/contratos/<contrato_id>/sincronizar", methods=["POST"])
    @jwt_required()
    def sincronizar_status(contrato_id):
        if request.data and request.get_json(silent=True) != {}:
            raise ErroAssinatura("Não envie dados para alterar o status.", 400)
        return jsonify(serializar(sincronizar(contrato_do_usuario(contrato_id))))

    @bp.route("/contratos/<contrato_id>/documento", methods=["GET"])
    @jwt_required()
    def documento(contrato_id):
        contrato = contrato_do_usuario(contrato_id)
        tipo = request.args.get("tipo", "original")
        if tipo not in {"original", "assinado"}:
            raise ErroAssinatura("Tipo de documento inválido.", 400)
        if tipo == "assinado":
            contrato = sincronizar(contrato)
            if contrato["status"] != "assinado":
                raise ErroAssinatura("O contrato ainda não foi assinado pelos dois participantes.", 409)
            pdf = baixar_pdf(contrato["_dropbox_id"])
        else:
            pdf = bytes(contrato["_pdf"])
        resposta = send_file(BytesIO(pdf), mimetype="application/pdf", as_attachment=True,
            download_name=f"contrato-{contrato_id}-{tipo}.pdf", max_age=0)
        resposta.headers["Cache-Control"] = "private, no-store"
        return resposta

    @bp.route("/webhooks/assinaturas", methods=["POST"])
    def webhook():
        try:
            evento = json.loads(request.form.get("json", ""))
            info = evento["event"]
            timestamp, tipo, signature = info["event_time"], info["event_type"], info["event_hash"]
            if not isinstance(tipo, str) or not str(timestamp).isdigit() or not isinstance(signature, str):
                raise ValueError
            esperado = hmac.new(chave_api().encode(), (str(timestamp) + tipo).encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(esperado, signature):
                raise ErroAssinatura("Notificação de assinatura inválida.", 401)
        except (ValueError, KeyError, TypeError):
            raise ErroAssinatura("Notificação de assinatura inválida.", 400) from None
        if tipo in EVENTOS_ASSINATURA:
            received = evento.get("signature_request") or {}
            if not isinstance(received, dict):
                raise ErroAssinatura("Notificação de assinatura inválida.", 400)
            request_id = identificador(received.get("signature_request_id"))
            colecao = conectar_db()["tbl_contratos"]
            contrato = colecao.find_one({"_dropbox_id": request_id})
            pedido = None
            if not contrato:
                # Recupera o vínculo após resposta perdida, sempre consultando o provedor.
                pedido = consultar_pedido(request_id)
                meta = pedido.get("metadata") or {}
                if not isinstance(meta, dict):
                    raise ErroAssinatura("Metadados da assinatura inválidos.", 400)
                if isinstance(meta.get("contrato_id"), str) and isinstance(meta.get("envio_ref"), str):
                    contrato = colecao.find_one({"_id": meta["contrato_id"], "_envio_ref": meta["envio_ref"]})
            if contrato:
                atualizar(colecao, contrato, pedido or consultar_pedido(request_id))
        return "Hello API Event Received", 200, {"Content-Type": "text/plain"}

    app.register_blueprint(bp)
