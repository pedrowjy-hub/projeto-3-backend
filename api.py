import os
from functools import lru_cache
from flask import Flask, jsonify
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

if __name__ == '__main__':
    app.run(debug=True)
