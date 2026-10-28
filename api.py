import os
import re
from io import BytesIO
from functools import lru_cache,wraps
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required, get_jwt_identity
)
from pymongo import MongoClient
from bson import ObjectId, Binary
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta
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


TAMANHO_MAXIMO_MIDIA_POSTAGEM = 8 * 1024 * 1024
MAXIMO_ARQUIVOS_POSTAGEM = 3
app.config["MAX_CONTENT_LENGTH"] = MAXIMO_ARQUIVOS_POSTAGEM * TAMANHO_MAXIMO_MIDIA_POSTAGEM + 256 * 1024


@app.errorhandler(413)
def requisicao_muito_grande(_erro):
    return jsonify({"erro": "Envie até três arquivos de no máximo 8 MB cada"}), 413


def serializar_postagem(postagem):
    postagem = dict(postagem)
    postagem["id"] = str(postagem.pop("_id"))
    if isinstance(postagem.get("usuario_id"), ObjectId):
        postagem["usuario_id"] = str(postagem["usuario_id"])
    midia = postagem.get("midia")
    if isinstance(midia, dict):
        midia = dict(midia)
        midia.pop("dados", None)
        postagem["midia"] = midia
    return postagem


def ler_midias_postagem(arquivos):
    if len(arquivos) > MAXIMO_ARQUIVOS_POSTAGEM:
        return None, (jsonify({"erro": "Adicione no máximo três arquivos por postagem"}), 400)
    midias = []
    for arquivo in arquivos:
        conteudo = arquivo.read(TAMANHO_MAXIMO_MIDIA_POSTAGEM + 1)
        if not arquivo.filename or not conteudo:
            return None, (jsonify({"erro": "O arquivo de mídia está vazio"}), 422)
        if len(conteudo) > TAMANHO_MAXIMO_MIDIA_POSTAGEM:
            return None, (jsonify({"erro": "Cada arquivo deve ter no máximo 8 MB"}), 413)
        midias.append({
            "dados": Binary(conteudo),
            "nome": os.path.basename(arquivo.filename.replace(chr(92), "/")),
            "tipo": arquivo.mimetype or "application/octet-stream",
        })
    return midias, None


def salvar_midias_extras(midias, postagem_id):
    # Cada documento fica abaixo de 16 MB, mesmo com três arquivos de 8 MB.
    metadados = [{"nome": item["nome"], "tipo": item["tipo"]} for item in midias]
    extras = []
    for indice, item in enumerate(midias[1:], 1):
        arquivo_id = ObjectId()
        extras.append({"_id": arquivo_id, "postagem_id": postagem_id, **item})
        metadados[indice]["id"] = str(arquivo_id)
    if extras:
        try:
            conectar_db()["tbl_midias_postagens"].insert_many(extras)
        except Exception:
            remover_midias_extras(metadados)
            raise
    return metadados


def remover_midias_extras(midias):
    ids = [ObjectId(item["id"]) for item in midias if item.get("id")]
    if ids:
        conectar_db()["tbl_midias_postagens"].delete_many({"_id": {"$in": ids}})


def obter_usuario_da_postagem(username):
    usuario = conectar_db()["tbl_usuarios"].find_one(
        {"username": username},
        {"_id": 1, "papel": 1},
    )
    return usuario


def validar_dados_postagem(dados):
    for campo in ("titulo", "descricao"):
        valor = dados.get(campo)
        if not isinstance(valor, str) or not valor.strip():
            return campo
    return None


def buscar_postagem_por_id(colecao, postagem_id, projecao=None):
    if not ObjectId.is_valid(postagem_id):
        return None
    return colecao.find_one({"_id": ObjectId(postagem_id)}, projecao)

def usuario_ou_admin_required(func):

    @wraps(func)
    @jwt_required()
    def wrapper(*args, **kwargs):
        username_logado = get_jwt_identity()
        user_logado  = conectar_db()["tbl_usuarios"].find_one(
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

        usuario = conectar_db()["tbl_usuarios"].find_one(
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

@app.route('/me', methods=['GET'])
@jwt_required()
def minha_conta():
    usuario = conectar_db()["tbl_usuarios"].find_one(
        {"username": get_jwt_identity()},
        {**CAMPOS_PUBLICOS, "email": 1, "contato": 1, "papel": 1,
         "assinatura": 1, "expiracao_assinatura": 1},
    )
    if usuario is None:
        return jsonify({"erro": "Usuário não encontrado"}), 404
    return jsonify(usuario), 200


@app.route('/usuarios',methods=['GET'])
def lista_users():
    tipo_perfil = request.args.get("tipo_perfil")

    filtro = {"papel":"usuario"}

    if tipo_perfil:
        if tipo_perfil not in TIPO_PERFIL:
            return jsonify({"erro":"tipo de perfil deve ser empresa ou influenciador"}),400

        filtro["tipo_perfil"] = tipo_perfil

    usuarios = list(
        conectar_db()['tbl_usuarios'].find(filtro, CAMPOS_PUBLICOS)
    )

    return jsonify(usuarios),200

@app.route('/usuarios/<string:username>', methods=['GET'])
def buscar_user(username):
    usuario = conectar_db()["tbl_usuarios"].find_one(
        {"username": username,"papel":"usuario"},
        CAMPOS_PUBLICOS,
    )

    if usuario is None:
        return jsonify({"erro": "Usuário não encontrado"}), 404
    return jsonify(usuario), 200

@app.route('/usuarios/<string:username>', methods=['DELETE'])
@usuario_ou_admin_required
def delete_user(username):

    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify({"erro": "JSON inválido"}), 400
    
    senha = dados.get("senha")
    if not isinstance(senha,str) or not senha.strip():
        return jsonify({"erro":"Por favor insira uma senha válida"}),400

    dados_user = conectar_db()["tbl_usuarios"].find_one(
        {"username":get_jwt_identity()},     
    )

    if dados_user is None:
        return jsonify({"erro":"Usuário não encontrado"}),404

    if not bcrypt.check_password_hash(dados_user["senha"],senha):
        return jsonify({"erro":"A senha inválida, por favor digite a senha do usuário atual"}),400

    usuario = conectar_db()["tbl_usuarios"].delete_one(
        {"username": username},
    )

    if usuario.deleted_count == 0:
        return jsonify({"erro": "Usuário não encontrado"}), 404
    return jsonify(), 204


def email_valido(email):
    if not isinstance(email,str):
        return False

    return re.fullmatch(
        r"[^@\s]+@[^@\s]+\.[^@\s]+",
        email.strip()
    ) is not None

def contato_valido(contato):
    if not isinstance(contato, str):
        return False

    numero = re.sub(r"[()\s-]", "", contato)
    return re.fullmatch(r"[0-9]{10,11}", numero) is not None


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

    if not email_valido(novo_usuario["email"]):
        return jsonify({"erro": "Formato de e-mail inválido"}), 400
    novo_usuario["email"] = novo_usuario["email"].strip()

    if not contato_valido(novo_usuario["contato"]):
        return jsonify({"erro": "Contato deve ter 10 ou 11 dígitos, incluindo o DDD"}), 400

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

    colecao = conectar_db()['tbl_usuarios']
    
    usuario_existente = colecao.find_one(
        {"username":novo_usuario["username"]}
    )

    if usuario_existente is not None:
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

    if "email" in dados:
        if not email_valido(dados["email"]):
            return jsonify({"erro": "Formato de e-mail inválido"}), 422

        dados["email"] = dados["email"].strip()

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

    if "contato" in dados and not contato_valido(dados["contato"]):
        return jsonify({"erro": "Contato deve ter 10 ou 11 dígitos, incluindo o DDD"}), 422

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

    colecao = conectar_db()["tbl_usuarios"]
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

    colecao = conectar_db()["tbl_usuarios"]

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

    colecao = conectar_db()["tbl_usuarios"]

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

def assinatura_premium_ativa(usuario, hoje=None):
    if usuario.get("assinatura") is not True:
        return False
    expiracao = usuario.get("expiracao_assinatura")
    if not isinstance(expiracao, str) or not re.fullmatch(r"\d{2}-\d{2}-\d{4}", expiracao):
        return False
    try:
        validade = datetime.strptime(expiracao, "%d-%m-%Y").date()
    except ValueError:
        return False
    # A validade inclui todo o último dia no horário de Brasília.
    hoje = hoje or datetime.now(timezone(timedelta(hours=-3))).date()
    return validade >= hoje


def obter_usuario_participante(id_influenciador, id_empresa):
    username_logado = get_jwt_identity()
    usuario_logado = conectar_db()["tbl_usuarios"].find_one(
        {"username": username_logado},
        {"_id": 0, "username": 1, "assinatura": 1, "expiracao_assinatura": 1},
    )

    if usuario_logado is None:
        return None, (jsonify({"erro": "Usuário não encontrado"}), 401)

    if username_logado not in {id_influenciador, id_empresa}:
        return None, (jsonify({"erro": "Acesso Negado"}), 403)

    if not assinatura_premium_ativa(usuario_logado):
        return None, (jsonify({
            "erro": "O chat é exclusivo para usuários com assinatura Premium ativa.",
            "codigo": "premium_necessario",
        }), 403)

    return username_logado, None

def buscar_chat(id_influenciador, id_empresa):
    return conectar_db()["tbl_chats"].find_one(
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

    colecao = conectar_db()["tbl_chats"]
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
    resultado = conectar_db()["tbl_chats"].update_one(
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
    resultado = conectar_db()["tbl_chats"].update_one(
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

    usuario_alvo = db["tbl_usuarios"].find_one({"username": username})

    if usuario_alvo is None:
        return jsonify({"erro": "Usuário alvo não encontrado"}), 404

    usuario_avaliador = db["tbl_usuarios"].find_one({"username": dados["username"]})

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

    usuarios = list(conectar_db()["tbl_usuarios"].find(
        {},
        {"_id":0,
         "senha":0,
        }
    ))

    return jsonify(usuarios),200

@app.route("/admin/usuario/<string:username>",methods=["GET"])
@admin_required
def admin_busca_user(username):
    usuario = conectar_db()["tbl_usuarios"].find_one(
            {"username": username,"papel":"usuario"},
            {"_id":0,
             "senha":0},
        )
    
    if usuario is None:
        return jsonify({"erro": "Usuário não encontrado"}), 404
    return jsonify(usuario), 200
        
@app.route("/admin",methods=["POST"])
@admin_required
def criar_admin():

    novo_admin = request.get_json(silent=True)

    if not isinstance(novo_admin,dict):
        return jsonify({"erro":"JSON inválido"}),400

    campos_obrigatorios = [
                "nome",
                "username",
                "email",
                "senha"
            ]

    campos_faltantes = [
            campo for campo in campos_obrigatorios
            if campo not in novo_admin
        ]
    
    if campos_faltantes:
        return jsonify({
        "erro": "Campos obrigatórios ausentes",
        "campos_faltantes": campos_faltantes
    }),400

    if not email_valido(novo_admin["email"]):
        return jsonify({"erro": "Formato de e-mail inválido"}), 400

    novo_admin["email"] = novo_admin["email"].strip()

    senha = novo_admin["senha"]

    if (not isinstance(senha,str) or not senha.strip()):
        return jsonify({"erro":"Senha no formato inválido"}),400

    novo_admin["senha"] = bcrypt.generate_password_hash(senha).decode("utf-8")

    colecao = conectar_db()['tbl_usuarios']
        
    usuario_existente = colecao.find_one(
        {"username":novo_admin["username"]}
    )

    if usuario_existente is not None:
        return jsonify(
            {"erro": "Conflito: username já cadastrado"}
        ),409

    novo_admin["papel"] = "admin"

    colecao.insert_one(novo_admin)

    return jsonify(
            {
            "mensagem": "Admin criado com sucesso",
            "usuario": novo_admin["username"],
        }),201

def verificar_permissao_postagem(usuario_id):
    username = get_jwt_identity()
    usuario = obter_usuario_da_postagem(username)
    if usuario is None:
        return None, (jsonify({"erro": "Usuário não encontrado"}), 401)

    if usuario.get("papel") != "admin" and usuario.get("_id") != usuario_id:
        return None, (jsonify({"erro": "Acesso negado"}), 403)
    return usuario, None


@app.route("/postagens", methods=["POST"])
@jwt_required()
def criar_postagem():
    if request.is_json:
        return jsonify({"erro": "Envie titulo, descricao e midia como multipart/form-data"}), 400

    dados = request.form
    arquivos = request.files.getlist("midia")
    campos_faltantes = [campo for campo in ("titulo", "descricao") if campo not in dados]
    if not arquivos or not any(arquivo.filename for arquivo in arquivos):
        campos_faltantes.append("midia")
    if campos_faltantes:
        return jsonify({
            "erro": "Campos obrigatórios ausentes",
            "campos_faltantes": campos_faltantes,
        }), 400

    campo_invalido = validar_dados_postagem(dados)
    if campo_invalido:
        return jsonify({"erro": f"Valor inválido para {campo_invalido}"}), 422

    midias, erro = ler_midias_postagem(arquivos)
    if erro:
        return erro

    usuario = obter_usuario_da_postagem(get_jwt_identity())
    if usuario is None:
        return jsonify({"erro": "Usuário não encontrado"}), 401

    postagem_id = ObjectId()
    metadados = salvar_midias_extras(midias, postagem_id)
    postagem = {
        "_id": postagem_id,
        "usuario_id": usuario["_id"],
        "titulo": dados["titulo"].strip(),
        "descricao": dados["descricao"].strip(),
        "midia": midias[0],
        "midias": metadados,
        "criada_em": datetime.now(timezone.utc),
    }

    try:
        resultado = conectar_db()["tbl_postagens"].insert_one(postagem)
    except Exception:
        remover_midias_extras(metadados)
        raise
    postagem["_id"] = resultado.inserted_id
    return jsonify({
        "mensagem": "Postagem criada com sucesso",
        "postagem": serializar_postagem(postagem),
    }), 201


@app.route("/postagens", methods=["GET"])
def listar_postagens():
    db = conectar_db()
    postagens = list(db["tbl_postagens"].find({}, {"midia.dados": 0}))
    ids_autores = list({item["usuario_id"] for item in postagens if item.get("usuario_id")})
    autores = {
        str(usuario["_id"]): {
            campo: usuario.get(campo) for campo in ("nome", "username", "tipo_perfil")
        }
        for usuario in db["tbl_usuarios"].find(
            {"_id": {"$in": ids_autores}},
            {"_id": 1, "nome": 1, "username": 1, "tipo_perfil": 1},
        )
    } if ids_autores else {}
    return jsonify([
        {**serializar_postagem(item), "autor": autores.get(str(item.get("usuario_id")))}
        for item in postagens
    ]), 200


@app.route("/postagens/<string:postagem_id>", methods=["GET"])
def buscar_postagem(postagem_id):
    postagem = buscar_postagem_por_id(
        conectar_db()["tbl_postagens"], postagem_id, {"midia.dados": 0}
    )
    if postagem is None:
        return jsonify({"erro": "Postagem não encontrada"}), 404
    return jsonify(serializar_postagem(postagem)), 200


@app.route("/postagens/<string:postagem_id>/midia", methods=["GET"])
@app.route("/postagens/<string:postagem_id>/midia/<int:indice>", methods=["GET"])
def buscar_midia_postagem(postagem_id, indice=0):
    postagem = buscar_postagem_por_id(conectar_db()["tbl_postagens"], postagem_id)
    if postagem is None or not isinstance(postagem.get("midia"), dict):
        return jsonify({"erro": "Mídia não encontrada"}), 404

    if indice == 0:
        midia = postagem["midia"]
    else:
        midias = postagem.get("midias", [])
        if indice >= len(midias) or not midias[indice].get("id"):
            return jsonify({"erro": "Mídia não encontrada"}), 404
        midia = conectar_db()["tbl_midias_postagens"].find_one({
            "_id": ObjectId(midias[indice]["id"]),
            "postagem_id": ObjectId(postagem_id),
        })
        if midia is None:
            return jsonify({"erro": "Mídia não encontrada"}), 404
    conteudo = midia.get("dados")
    if conteudo is None:
        return jsonify({"erro": "Mídia não encontrada"}), 404

    return send_file(
        BytesIO(bytes(conteudo)),
        mimetype=midia.get("tipo", "application/octet-stream"),
        download_name=midia.get("nome", "midia"),
        as_attachment=True,
    )


@app.route("/postagens/<string:postagem_id>", methods=["PATCH"])
@jwt_required()
def editar_postagem(postagem_id):
    if not ObjectId.is_valid(postagem_id):
        return jsonify({"erro": "Postagem não encontrada"}), 404

    if request.is_json:
        dados = request.get_json(silent=True)
        arquivos = []
    else:
        dados = request.form.to_dict()
        arquivos = request.files.getlist("midia")

    if not isinstance(dados, dict):
        return jsonify({"erro": "JSON inválido"}), 400

    campos_desconhecidos = set(dados) - {"titulo", "descricao"}
    if campos_desconhecidos:
        return jsonify({
            "erro": "Campos não permitidos",
            "campos": sorted(campos_desconhecidos),
        }), 400
    if not dados and not arquivos:
        return jsonify({"erro": "Envie ao menos um campo para editar"}), 400

    for campo, valor in dados.items():
        if not isinstance(valor, str) or not valor.strip():
            return jsonify({"erro": f"Valor inválido para {campo}"}), 422

    atualizacao = {campo: valor.strip() for campo, valor in dados.items()}
    midias = None
    if arquivos:
        midias, erro = ler_midias_postagem(arquivos)
        if erro:
            return erro

    colecao = conectar_db()["tbl_postagens"]
    postagem = buscar_postagem_por_id(colecao, postagem_id)
    if postagem is None:
        return jsonify({"erro": "Postagem não encontrada"}), 404

    _, erro = verificar_permissao_postagem(postagem["usuario_id"])
    if erro:
        return erro

    if midias:
        atualizacao["midia"] = midias[0]
        atualizacao["midias"] = salvar_midias_extras(midias, ObjectId(postagem_id))
    try:
        colecao.update_one(
            {"_id": ObjectId(postagem_id)},
            {"$set": atualizacao},
        )
    except Exception:
        if midias:
            remover_midias_extras(atualizacao["midias"])
        raise
    if midias:
        remover_midias_extras(postagem.get("midias", []))
    return jsonify({"mensagem": "Postagem atualizada com sucesso"}), 200


@app.route("/postagens/<string:postagem_id>", methods=["DELETE"])
@jwt_required()
def deletar_postagem(postagem_id):
    if not ObjectId.is_valid(postagem_id):
        return jsonify({"erro": "Postagem não encontrada"}), 404

    colecao = conectar_db()["tbl_postagens"]
    postagem = buscar_postagem_por_id(colecao, postagem_id)
    if postagem is None:
        return jsonify({"erro": "Postagem não encontrada"}), 404

    _, erro = verificar_permissao_postagem(postagem["usuario_id"])
    if erro:
        return erro

    colecao.delete_one({"_id": ObjectId(postagem_id)})
    remover_midias_extras(postagem.get("midias", []))
    return jsonify(), 204


if __name__ == '__main__':
    app.run(debug=True)
