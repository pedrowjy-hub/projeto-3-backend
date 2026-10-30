"""Conversa autenticada que vincula negociação, participantes e contratos."""
import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from bson import ObjectId
from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pymongo.errors import DuplicateKeyError, PyMongoError

from assinaturas import premium_ativo
from dropbox_sign import ErroAssinatura


def usuario_da_conversa(db, username, premium=True):
    user = db["tbl_usuarios"].find_one({"username": username, "papel": "usuario", "ativo": {"$ne": False}})
    if not user or user.get("tipo_perfil") not in {"empresa", "influenciador"}:
        raise ErroAssinatura("Participante não encontrado.", 404)
    if premium and not premium_ativo(user):
        raise ErroAssinatura("Esta operação exige Premium ativo.", 403)
    return user


def acessar_conversa(db, chat_id, username):
    actor = usuario_da_conversa(db, username)
    if not isinstance(chat_id, str) or not ObjectId.is_valid(chat_id):
        raise ErroAssinatura("Conversa não encontrada.", 404)
    chat = db["tbl_chats"].find_one({"_id": ObjectId(chat_id)})
    if not chat or username not in {chat.get("id_empresa"), chat.get("id_influenciador")}:
        raise ErroAssinatura("Conversa não encontrada.", 404)
    for role in ("empresa", "influenciador"):
        pessoa = usuario_da_conversa(db, chat.get("id_" + role), premium=False)
        if pessoa["tipo_perfil"] != role:
            raise ErroAssinatura("Os participantes desta conversa precisam ser conferidos.", 409)
    return chat, actor


def registrar_conversas(app, conectar_db):
    bp = Blueprint("conversas", __name__)

    @bp.errorhandler(ErroAssinatura)
    def erro_operacao(error):
        return jsonify({"erro": str(error)}), error.status

    @bp.errorhandler(PyMongoError)
    def erro_banco(_error):
        return jsonify({"erro": "Não foi possível atualizar a conversa. Tente novamente."}), 503

    def serializar(chat, username):
        outro = chat["id_influenciador"] if username == chat["id_empresa"] else chat["id_empresa"]
        partner = usuario_da_conversa(conectar_db(), outro, premium=False)
        return {"id": str(chat["_id"]), "id_empresa": chat["id_empresa"], "id_influenciador": chat["id_influenciador"],
            "parceiro": {"username": partner["username"], "nome": partner.get("nome") or outro, "tipo_perfil": partner["tipo_perfil"]},
            "atualizado_em": chat.get("atualizado_em", "")}

    @bp.route("/conversas", methods=["GET", "POST"])
    @jwt_required()
    def conversas():
        db = conectar_db()
        actor = usuario_da_conversa(db, get_jwt_identity())
        colecao = db["tbl_chats"]
        if request.method == "GET":
            docs = colecao.find({"$or": [{"id_empresa": actor["username"]}, {"id_influenciador": actor["username"]}]},
                {"_id": 1, "id_empresa": 1, "id_influenciador": 1, "atualizado_em": 1}).sort("atualizado_em", -1).limit(100)
            result = []
            for doc in docs:
                try: result.append(serializar(doc, actor["username"]))
                except ErroAssinatura: continue  # Contas removidas não expõem conversas a terceiros.
            return jsonify(result)
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {"parceiro"} or not isinstance(body["parceiro"], str):
            raise ErroAssinatura("Escolha o perfil com quem deseja conversar.", 400)
        partner = usuario_da_conversa(db, body["parceiro"].strip())
        if {actor["tipo_perfil"], partner["tipo_perfil"]} != {"empresa", "influenciador"}:
            raise ErroAssinatura("A conversa deve ter uma empresa e um influenciador.", 400)
        pair = {"id_" + pessoa["tipo_perfil"]: pessoa["username"] for pessoa in (actor, partner)}
        existing = colecao.find_one(pair)
        if existing: return jsonify(serializar(existing, actor["username"])), 200
        digest = hashlib.sha256(json.dumps(pair, sort_keys=True).encode()).hexdigest()
        chat = {"_id": ObjectId(digest[:24]), **pair, "conversa": [], "arquivos": [], "atualizado_em": datetime.now(timezone.utc).isoformat()}
        try: colecao.insert_one(chat)
        except DuplicateKeyError:
            existing = colecao.find_one(pair)
            if not existing: raise
            return jsonify(serializar(existing, actor["username"])), 200
        return jsonify(serializar(chat, actor["username"])), 201

    @bp.route("/conversas/<chat_id>/mensagens", methods=["GET", "POST"])
    @jwt_required()
    def mensagens(chat_id):
        db = conectar_db()
        chat, actor = acessar_conversa(db, chat_id, get_jwt_identity())
        if request.method == "GET":
            values = [item for item in chat.get("conversa", []) if isinstance(item, dict)
                and item.get("remetente_id") in {chat["id_empresa"], chat["id_influenciador"]} and isinstance(item.get("mensagem"), str)]
            return jsonify({"mensagens": values[-200:], "tem_anteriores": len(values) > 200})
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {"mensagem"} or not isinstance(body["mensagem"], str) or not 1 <= len(body["mensagem"].strip()) <= 4000:
            raise ErroAssinatura("Envie uma mensagem de 1 a 4.000 caracteres.", 400)
        message = {"id": str(uuid4()), "remetente_id": actor["username"], "mensagem": body["mensagem"].strip(), "criado_em": datetime.now(timezone.utc).isoformat()}
        result = db["tbl_chats"].update_one({"_id": chat["_id"]}, {"$push": {"conversa": message}, "$set": {"atualizado_em": message["criado_em"]}})
        if result.matched_count != 1: raise ErroAssinatura("Conversa não encontrada.", 404)
        return jsonify(message), 201

    app.register_blueprint(bp)
