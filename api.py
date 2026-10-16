import os
from functools import lru_cache
from flask import Flask, jsonify, request
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()
app =  Flask(__name__)


@lru_cache
def get_client():
    return MongoClient(
        os.environ["MONGO_URI"],
        serverSelectionTimeoutMS=5000,
    )

def conectar_db():
    return get_client()[os.getenv("MONGO_DB", "projeto3")]


@app.route('/usuarios',methods=['GET'])
def lista_users():
    usuarios = list(
        conectar_db()['tbl_testes'].find(
            {},
            {"_id":0},
        )
    )

    return  jsonify(usuarios),200

@app.route('/usuarios/<string:username>', methods=['GET'])
def buscar_user(username):
    usuario = conectar_db()["tbl_testes"].find_one(
        {"username": username},
        {"_id": 0},
    )

    if usuario is None:
        return jsonify({"erro": "Usuário não encontrado"}), 404
    return jsonify(usuario), 200

@app.route('/usuarios/<string:username>', methods=['DELETE'])
def delete_user(username):
    usuario = conectar_db()["tbl_testes"].delete_one(
        {"username": username},
    )

    if usuario.deleted_count == 0:
        return jsonify({"erro": "Usuário não encontrado"}), 404
    return jsonify(), 204

@app.route("/usuarios",methods=['POST'])
def criar_user():

    novo_usuario = request.get_json(silent=True)

    campos_obrigatorios = [
            "nome",
            "username",
            "email",
            "contato",
            "tipo",
            "nicho",
            "plataformas",
            "assinatura"
        ]

    campos_faltantes = [
        campo for campo in campos_obrigatorios
        if campo not in novo_usuario
    ]

    if campos_faltantes:
        return jsonify({
        "erro": "Campos obrigatórios ausentes",
        "campos_faltantes": campos_faltantes
    }),400

    colecao = conectar_db()['tbl_teste']
    usuario_existente = colecao.find_one(
        {"username":novo_usuario["username"]}
    )

    if isinstance(usuario_existente, dict):
        return jsonify(
            {"erro": "Conflito: username já cadastrado"}
        ),409

    colecao.insert_one(novo_usuario)

    return jsonify(
        {
        "mensagem": "Usuário criado com sucesso",
        "usuario": novo_usuario["username"],
    }),201

@app.route('/usuarios/<string:username>', methods=['PUT'])
def editar_user(username):
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify({"erro": "JSON inválido"}), 400

    campos_obrigatorios = ["nome", "email", "contato", "tipo", "nicho", "plataformas"]
    campos_faltantes = [campo for campo in campos_obrigatorios if campo not in dados]
    if campos_faltantes:
        return jsonify({
            "erro": "Campos obrigatórios ausentes",
            "campos_faltantes": campos_faltantes,
        }), 400

    for campo in ["nome", "email", "contato", "tipo", "nicho"]:
        if not isinstance(dados[campo], str) or not dados[campo].strip():
            return jsonify({"erro": f"Valor inválido para {campo}"}), 422

    if not isinstance(dados["plataformas"], list) or not all(
        isinstance(plataforma, str) and plataforma.strip()
        for plataforma in dados["plataformas"]
    ):
        return jsonify({"erro": "Valor inválido para plataformas"}), 422

    colecao = conectar_db()["tbl_testes"]
    usuario = colecao.find_one({"username": username})
    if usuario is None:
        return jsonify({"erro": "Usuário não encontrado"}), 404

    for campo in ["email", "contato"]:
        conflito = colecao.find_one({campo: dados[campo], "username": {"$ne": username}})
        if conflito is not None:
            return jsonify({"erro": f"Conflito: {campo} já cadastrado"}), 409

    atualizacao = {campo: dados[campo] for campo in campos_obrigatorios}
    resultado = colecao.update_one({"username": username}, {"$set": atualizacao})
    if resultado.matched_count == 0:
        return jsonify({"erro": "Usuário não encontrado"}), 404

    return jsonify({"mensagem": "Usuário atualizado com sucesso"}), 200


def buscar_chat(id_influenciador, id_empresa):
    return conectar_db()["chats"].find_one(
        {
            "id_influenciador": id_influenciador,
            "id_empresa": id_empresa,
        },
        {"_id": 0},
    )


@app.route("/chats", methods=["POST"])
def criar_chat():
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify({"erro": "JSON inválido"}), 400

    campos_obrigatorios = ["id_influenciador", "id_empresa"]
    campos_faltantes = [
        campo for campo in campos_obrigatorios if campo not in dados
    ]
    if campos_faltantes:
        return jsonify({
            "erro": "Campos obrigatórios ausentes",
            "campos_faltantes": campos_faltantes,
        }), 400

    if not all(
        isinstance(dados[campo], str) and dados[campo].strip()
        for campo in campos_obrigatorios
    ):
        return jsonify({"erro": "IDs dos usuários inválidos"}), 422

    colecao = conectar_db()["chats"]
    filtro = {
        "id_influenciador": dados["id_influenciador"],
        "id_empresa": dados["id_empresa"],
    }
    if colecao.find_one(filtro) is not None:
        return jsonify({"erro": "Chat já existe"}), 409

    chat = {
        **filtro,
        "conversa": [],
        "arquivos": [],
    }
    colecao.insert_one(chat)
    return jsonify({"mensagem": "Chat criado com sucesso", "chat": chat}), 201


@app.route("/chats/<string:id_influenciador>/<string:id_empresa>", methods=["GET"])
def buscar_chat_por_usuarios(id_influenciador, id_empresa):
    chat = buscar_chat(id_influenciador, id_empresa)
    if chat is None:
        return jsonify({"erro": "Chat não encontrado"}), 404
    return jsonify(chat), 200


@app.route("/chats/<string:id_influenciador>/<string:id_empresa>/mensagens", methods=["POST"])
def enviar_mensagem(id_influenciador, id_empresa):
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify({"erro": "JSON inválido"}), 400

    campos_obrigatorios = ["remetente_id", "mensagem"]
    campos_faltantes = [
        campo for campo in campos_obrigatorios if campo not in dados
    ]
    if campos_faltantes:
        return jsonify({
            "erro": "Campos obrigatórios ausentes",
            "campos_faltantes": campos_faltantes,
        }), 400

    if (
        dados["remetente_id"] not in {id_influenciador, id_empresa}
        or not isinstance(dados["mensagem"], str)
        or not dados["mensagem"].strip()
    ):
        return jsonify({"erro": "Mensagem inválida"}), 422

    filtro = {
        "id_influenciador": id_influenciador,
        "id_empresa": id_empresa,
    }
    resultado = conectar_db()["chats"].update_one(
        filtro,
        {"$push": {"conversa": dados}},
    )
    if resultado.matched_count == 0:
        return jsonify({"erro": "Chat não encontrado"}), 404
    return jsonify({"mensagem": "Mensagem enviada com sucesso"}), 201


@app.route("/chats/<string:id_influenciador>/<string:id_empresa>/arquivos", methods=["POST"])
def adicionar_arquivo(id_influenciador, id_empresa):
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify({"erro": "JSON inválido"}), 400

    campos_obrigatorios = ["nome", "url", "tipo"]
    campos_faltantes = [
        campo for campo in campos_obrigatorios if campo not in dados
    ]
    if campos_faltantes:
        return jsonify({
            "erro": "Campos obrigatórios ausentes",
            "campos_faltantes": campos_faltantes,
        }), 400

    if not all(
        isinstance(dados[campo], str) and dados[campo].strip()
        for campo in campos_obrigatorios
    ):
        return jsonify({"erro": "Arquivo inválido"}), 422

    filtro = {
        "id_influenciador": id_influenciador,
        "id_empresa": id_empresa,
    }
    resultado = conectar_db()["chats"].update_one(
        filtro,
        {"$push": {"arquivos": dados}},
    )
    if resultado.matched_count == 0:
        return jsonify({"erro": "Chat não encontrado"}), 404
    return jsonify({"mensagem": "Arquivo adicionado com sucesso"}), 201

if __name__ == '__main__':
    app.run(debug=True)
