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
            "assinatura",
            "expiracao_assinatura",
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

if __name__ == '__main__':
    app.run(debug=True)
