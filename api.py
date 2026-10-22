import os
from functools import lru_cache,wraps
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required, get_jwt_identity
)
from pymongo import MongoClient
from dotenv import load_dotenv
from datetime import datetime
from flask_bcrypt import Bcrypt

load_dotenv()
app =  Flask(__name__)
CORS(app)
bcrypt = Bcrypt(app)
app.config['JWT_SECRET_KEY'] = os.getenv("JWT_SECRET_KEY")
jwt = JWTManager(app)


@lru_cache
def get_client():
    return MongoClient(
        os.environ["MONGO_URI"],
        serverSelectionTimeoutMS=5000,
    )

def conectar_db():
    return get_client()[os.getenv("MONGO_DB", "projeto3")]

def usuario_ou_admin_required(func):

    @wraps(func)
    @jwt_required()
    def wrapper(*args, **kwargs):
        username_logado = get_jwt_identity()
        user_logado  = conectar_db()["tbl_testes"].find_one(
            {"username":username_logado},
            {"_id":0,"papel":1}
        )

        if user_logado is None:
            return jsonify({"erro":"Usuário não encontrado"}),401

        username_alvo = kwargs["username"]
        eh_admin = user_logado.get("papel") == "admin"

        if username_logado != username_alvo and not eh_admin:
            return jsonify({"erro":"Acesso Negado"}),403

        return func(*args,**kwargs)

    return wrapper

def admin_required(func):
    @wraps(func)
    @jwt_required()

    def wrapper(*args,**kwargs):
        username = get_jwt_identity()

        usuario = conectar_db()["tbl_testes"].find_one(
            {"username":username},
            {"_id":0},
        )

        if usuario is None:
            return jsonify({"erro":"Usuário não encontrado"}),404

        if usuario.get("papel") != "admin":
            return jsonify({"erro":"Acesso negado"}),403

        return func(*args,**kwargs)
    return wrapper

TIPO_PERFIL = {"empresa","influenciador"}
CAMPOS_PUBLICOS = {
    "_id":0,
    "username":1,
    "nome":1,
    "tipo_perfil":1,
    "nicho":1,
    "plataformas":1,
    "descricao":1,
}

@app.route('/usuarios',methods=['GET'])
def lista_users():
    tipo_perfil = request.args.get("tipo_perfil")

    filtro = {"papel":"usuario"}

    if tipo_perfil:
        if tipo_perfil not in TIPO_PERFIL:
            return jsonify({"erro":"tipo de perfil deve ser empresa ou influenciador"}),400

        filtro["tipo_perfil"] = tipo_perfil

    usuarios = list(
        conectar_db()['tbl_testes'].find(filtro, CAMPOS_PUBLICOS)
    )

    return jsonify(usuarios),200

@app.route('/usuarios/<string:username>', methods=['GET'])
def buscar_user(username):
    usuario = conectar_db()["tbl_testes"].find_one(
        {"username": username,"papel":"usuario"},
        CAMPOS_PUBLICOS,
    )

    if usuario is None:
        return jsonify({"erro": "Usuário não encontrado"}), 404
    return jsonify(usuario), 200

@app.route('/usuarios/<string:username>', methods=['DELETE'])
@usuario_ou_admin_required
def delete_user(username):
    usuario = conectar_db()["tbl_testes"].delete_one(
        {"username": username},
    )

    if usuario.deleted_count == 0:
        return jsonify({"erro": "Usuário não encontrado"}), 404
    return jsonify(), 204


@app.route("/usuarios",methods=['POST'])
def criar_user():

    novo_usuario = request.get_json(silent=True) or {}

    if not isinstance(novo_usuario,dict):
        return jsonify({"erro":"JSON inválido"}),400

    if "papel" in novo_usuario:
        return jsonify({"erro":"Papel é definido pelo servidor"}),400

    campos_obrigatorios = [
            "nome",
            "username",
            "email",
            "contato",
            "tipo_perfil",
            "nicho",
            "plataformas",
            "senha"
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

    tipo_perfil = novo_usuario["tipo_perfil"]
    if not isinstance(tipo_perfil, str) or tipo_perfil not in TIPO_PERFIL:
        return jsonify({"erro":"Tipo de perfil deve ser Influenciador ou Empresa"}),400

    senha = novo_usuario.get("senha")

    if not isinstance(senha,str) or not senha:
        return jsonify({"erro": "Senha inválida"}),400

    novo_usuario["senha"] = bcrypt.generate_password_hash(senha).decode("utf-8")

    if (
        not isinstance(novo_usuario["plataformas"], list)
        or not novo_usuario["plataformas"]
        or not all(
            isinstance(plataforma, str) and plataforma.strip()
            for plataforma in novo_usuario["plataformas"]
        )
    ):
        return jsonify({"erro": "Plataformas inválidas"}), 400

    colecao = conectar_db()['tbl_testes']
    
    usuario_existente = colecao.find_one(
        {"username":novo_usuario["username"]}
    )

    if isinstance(usuario_existente, dict):
        return jsonify(
            {"erro": "Conflito: username já cadastrado"}
        ),409
    
    novo_usuario["papel"]="usuario"
    
    colecao.insert_one(novo_usuario)

    return jsonify(
        {
        "mensagem": "Usuário criado com sucesso",
        "usuario": novo_usuario["username"],
    }),201

@app.route('/usuarios/<string:username>', methods=['PATCH'])
@usuario_ou_admin_required
def editar_user(username):
    dados = request.get_json(silent=True)

    if not isinstance(dados, dict) or not dados:
        return jsonify({"erro": "Envie ao menos um campo para editar"}), 400

    campos_editaveis = {
        "nome", "email", "contato", "tipo_perfil",
        "nicho", "plataformas", "descricao",
    }

    if "papel" in dados:
        return jsonify({"erro":"Papel não pode ser alterado"}),400

    campos_desconhecidos = set(dados) - campos_editaveis
    if campos_desconhecidos:
        return jsonify({
            "erro": "Campos não permitidos",
            "campos": list(campos_desconhecidos),
        }), 400

    for campo in ["nome", "email", "contato", "tipo_perfil", "nicho"]:
        if campo in dados and (
            not isinstance(dados[campo], str) or not dados[campo].strip()
        ):
            return jsonify({"erro": f"Valor inválido para {campo}"}), 422

    if "plataformas" in dados:
        plataformas = dados["plataformas"]
        if (
            not isinstance(plataformas, list)
            or not plataformas
            or not all(
                isinstance(plataforma, str) and plataforma.strip()
                for plataforma in plataformas
            )
        ):
            return jsonify({"erro": "Valor inválido para plataformas"}), 422

    if "descricao" in dados and not isinstance(dados["descricao"], str):
        return jsonify({"erro": "Valor inválido para descricao"}), 422

    if ("tipo_perfil" in dados and dados["tipo_perfil"] not in TIPO_PERFIL):
        return jsonify({"erro":"Tipo de perfil deve ser empresa ou influenciador"}),422

    colecao = conectar_db()["tbl_testes"]
    if colecao.find_one({"username": username}) is None:
        return jsonify({"erro": "Usuário não encontrado"}), 404

    for campo in ["email", "contato"]:
        if campo in dados:
            conflito = colecao.find_one({
                campo: dados[campo],
                "username": {"$ne": username},
            })
            if conflito is not None:
                return jsonify({"erro": f"Conflito: {campo} já cadastrado"}), 409

    atualizacao = {}
    for campo, valor in dados.items():
        if isinstance(valor, str):
            atualizacao[campo] = valor.strip()
        else:
            atualizacao[campo] = valor

    colecao.update_one(
        {"username": username},
        {"$set": atualizacao},
    )

    return jsonify({"mensagem": "Usuário atualizado com sucesso"}), 200

@app.route("/usuarios/<string:username>/assinatura", methods=['PATCH'])
@jwt_required()
def alterar_assinatura(username):
    username_logado = get_jwt_identity()

    if username_logado != username:
        return jsonify({"erro":"Acesso Negado"}),403
        
    dados = request.get_json(silent=True)

    if not isinstance(dados, dict):
        return jsonify({"erro": "JSON inválido"}), 400

    assinatura = dados.get("assinatura")

    if not isinstance(assinatura, bool):
        return jsonify({"erro": "assinatura deve ser um booleano"}), 400

    if assinatura:
        expiracao = dados.get("expiracao_assinatura")

        if not expiracao:
            return jsonify({"erro": "expiracao_assinatura é obrigatória para ativar a assinatura"}), 400

        try:
            datetime.strptime(expiracao, "%d-%m-%Y")
        except (ValueError, TypeError):
            return jsonify({
                "erro": "expiracao_assinatura deve ser uma data válida no formato DD-MM-AAAA"
            }), 400

        atualizacao = {
            "$set": {
                "assinatura": True,
                "expiracao_assinatura": expiracao,
            }
        }
    else:
        atualizacao = {
            "$set": {"assinatura": False},
            "$unset": {"expiracao_assinatura": ""},
        }

    colecao = conectar_db()["tbl_testes"]

    usuario = colecao.find_one({"username": username})
    if usuario is None:
        return jsonify({"erro": "Usuário não encontrado"}), 404

    resultado = colecao.update_one(
        {"username": username},
        atualizacao,
    )

    if resultado.matched_count == 0:
        return jsonify({"erro": "Usuário não encontrado"}), 404

    return jsonify({
        "mensagem": "Assinatura atualizada com sucesso"
    }), 200

@app.route("/login",methods=["POST"])
def login():
    dados = request.get_json(silent=True) or {}

    identificador = dados.get("identificador")
    senha = dados.get("senha")

    if (
        not isinstance(identificador,str) 
        or not identificador.strip()
        or not isinstance(senha,str)
        or not senha
    ):
        return jsonify({
            "erro": "Identificador e senha são obrigatórios"
        }),400

    colecao = conectar_db()["tbl_testes"]

    usuario = colecao.find_one({
        "$or": [
            {"username": identificador},
            {"email": identificador},
        ]
    })

    if usuario is None or not bcrypt.check_password_hash(usuario.get("senha"),dados.get("senha")):
        return jsonify({"erro": "Credenciais inválidas"}),401

    token = create_access_token(identity=usuario["username"],
                                additional_claims={"email":usuario["email"]})
    return jsonify(access_token = token),200

def obter_usuario_participante(id_influenciador, id_empresa):
    username_logado = get_jwt_identity()
    usuario_logado = conectar_db()["tbl_testes"].find_one(
        {"username": username_logado},
        {"_id": 0, "username": 1},
    )

    if usuario_logado is None:
        return None, (jsonify({"erro": "Usuário não encontrado"}), 401)

    if username_logado not in {id_influenciador, id_empresa}:
        return None, (jsonify({"erro": "Acesso Negado"}), 403)

    return username_logado, None

def buscar_chat(id_influenciador, id_empresa):
    return conectar_db()["chats"].find_one(
        {
            "id_influenciador": id_influenciador,
            "id_empresa": id_empresa,
        },
        {"_id": 0},
    )


@app.route("/chats", methods=["POST"])
@jwt_required()
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

    _, erro = obter_usuario_participante(
        dados["id_influenciador"], dados["id_empresa"]
    )
    if erro:
        return erro

    colecao = conectar_db()["chats"]
    filtro = {
        "id_influenciador": dados["id_influenciador"],
        "id_empresa": dados["id_empresa"],
    }
    if colecao.find_one(filtro) is not None:
        return jsonify({"erro": "Chat já existe"}), 409

    chat = {**filtro, "conversa": [], "arquivos": []}
    colecao.insert_one(chat)
    return jsonify({"mensagem": "Chat criado com sucesso", "chat": chat}), 201


@app.route("/chats/<string:id_influenciador>/<string:id_empresa>", methods=["GET"])
@jwt_required()
def buscar_chat_por_usuarios(id_influenciador, id_empresa):
    _, erro = obter_usuario_participante(id_influenciador, id_empresa)
    if erro:
        return erro
    
    chat = buscar_chat(id_influenciador, id_empresa)
    if chat is None:
        return jsonify({"erro": "Chat não encontrado"}), 404
    return jsonify(chat), 200


@app.route("/chats/<string:id_influenciador>/<string:id_empresa>/mensagens", methods=["POST"])
@jwt_required()
def enviar_mensagem(id_influenciador, id_empresa):
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify({"erro": "JSON inválido"}), 400

    if not isinstance(dados.get("mensagem"),str) or not dados["mensagem"].strip():
        return jsonify({"erro":"Mensagem inválida"}),422

    username_logado, erro = obter_usuario_participante(
        id_influenciador, id_empresa
    )
    if erro:
        return erro

    mensagem = {
        "remetente_id": username_logado,
        "mensagem": dados["mensagem"],
    }
    
    filtro = {
        "id_influenciador": id_influenciador,
        "id_empresa": id_empresa,
    }
    resultado = conectar_db()["chats"].update_one(
        filtro,
        {"$push": {"conversa": mensagem}},
    )
    if resultado.matched_count == 0:
        return jsonify({"erro": "Chat não encontrado"}), 404
    return jsonify({"mensagem": "Mensagem enviada com sucesso"}), 201


@app.route("/chats/<string:id_influenciador>/<string:id_empresa>/arquivos", methods=["POST"])
@jwt_required()
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

    _, erro = obter_usuario_participante(id_influenciador, id_empresa)
    if erro:
        return erro

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

@app.route("/usuarios/<string:username>/avaliacoes", methods=['POST'])
def avaliar(username):
    dados = request.get_json(silent=True)

    if not isinstance(dados, dict):
        return jsonify({"erro": "JSON inválido"}), 400

    campos_obrigatorios = ["username", "alvo", "nota", "conteudo"]
    campos_faltantes = [campo for campo in campos_obrigatorios if campo not in dados]

    if campos_faltantes:
        return jsonify({
            "erro": "Campos obrigatórios ausentes",
            "campos_faltantes": campos_faltantes,
        }), 400

    for campo in ["username", "alvo", "conteudo"]:
        if not isinstance(dados[campo], str) or not dados[campo].strip():
            return jsonify({"erro": f"Valor inválido para {campo}"}), 422

    if type(dados["nota"]) is not int or not 1 <= dados["nota"] <= 5:
        return jsonify({"erro": "nota deve ser um inteiro entre 1 e 5"}), 422

    if dados["alvo"] != username:
        return jsonify({
            "erro": "alvo deve corresponder ao usuário da URL"
        }), 422

    db = conectar_db()

    usuario_alvo = db["tbl_testes"].find_one({"username": username})

    if usuario_alvo is None:
        return jsonify({"erro": "Usuário alvo não encontrado"}), 404

    usuario_avaliador = db["tbl_testes"].find_one({"username": dados["username"]})

    if usuario_avaliador is None:
        return jsonify({"erro": "Usuário criador não encontrado"}), 404

    avaliacao = {
        campo: dados[campo]
        for campo in campos_obrigatorios
    }

    db["tbl_avaliacoes"].insert_one(avaliacao)

    return jsonify({"mensagem": "Avaliação feita com sucesso"}), 201

@app.route("/admin", methods=['GET'])
@admin_required
def admin_panel():    
    return jsonify({"mensagem": "Acesso concedido, bem-vindo chefe"}), 200

@app.route("/admin/usuarios",methods=["GET"])
@admin_required
def admin_listar_user():

    usuarios = list(conectar_db()["tbl_testes"].find(
        {},
        {"_id":0,
         "senha":0,
        }
    ))

    return jsonify(usuarios),200

@app.route("/admin/usuario/<string:username>",methods=["GET"])
@admin_required
def admin_busca_user(username):
    usuario = conectar_db()["tbl_testes"].find_one(
            {"username": username,"papel":"usuario"},
            {"_id":0,
             "senha":0},
        )
    
    if usuario is None:
        return jsonify({"erro": "Usuário não encontrado"}), 404
    return jsonify(usuario), 200
        


if __name__ == '__main__':
    app.run(debug=True)
