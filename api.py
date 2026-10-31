import os
import re
from io import BytesIO
from functools import lru_cache,wraps
from threading import Lock
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required, get_jwt_identity
)
from pymongo import MongoClient, ReturnDocument
from preferencias import validar_preferencias, preferencias_usuario
from pymongo.errors import DuplicateKeyError, PyMongoError
from uuid import uuid4
from pagamentos import ErroPagamento, configuracao_paddle, requisitar_paddle, validar_preco, validar_transacao
from assinaturas import verificar_evento, processar_evento, cancelar_renovacao, premium_ativo, campos_assinatura, CAMPOS_LEGADOS
from bson import ObjectId, Binary
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta
from flask_bcrypt import Bcrypt
from urllib.parse import urlparse
import requests

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
YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3"


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

@jwt.token_in_blocklist_loader
def token_conta_desativada(_header, payload):
    usuario = conectar_db()["tbl_usuarios"].find_one({"username": payload["sub"]})
    return usuario is None or usuario.get("ativo", True) is False


@jwt.revoked_token_loader
def conta_desativada_response(_header, _payload):
    return jsonify({"erro": "Conta desativada ou indisponível. Entre em contato com a administração."}), 401


TIPO_PERFIL = {"empresa","influenciador"}
CAMPOS_PUBLICOS = {
    "_id":0,
    "username":1,
    "nome":1,
    "tipo_perfil":1,
    "nicho":1,
    "plataformas":1,
    "descricao":1,
    "cidade":1,
    "estado":1,
}

@app.route('/me', methods=['GET'])
@jwt_required()
def minha_conta():
    usuario = conectar_db()["tbl_usuarios"].find_one(
        {"username": get_jwt_identity()},
        {**CAMPOS_PUBLICOS, "email": 1, "contato": 1, "papel": 1,
         "pagamento": 1, "assinatura": 1, "expiracao_assinatura": 1, "premium_ate": 1,
         "paddle_status": 1, "paddle_subscription_id": 1, "cancelamento_agendado": 1,
         "renovacao_automatica": 1, "proxima_renovacao": 1, "falha_pagamento": 1},
    )
    if usuario is None:
        return jsonify({"erro": "Usuário não encontrado"}), 404
    if "pagamento" in usuario:
        usuario.update(campos_assinatura(usuario))
        usuario.pop("pagamento")  # cliente_id e demais dados internos não saem na API.
        usuario.pop("expiracao_assinatura", None)
    elif "premium_ate" in usuario:
        usuario["assinatura"] = premium_ativo(usuario)
    return jsonify(usuario), 200


@app.route('/usuarios/me/preferencias', methods=['GET', 'PATCH'])
@jwt_required()
def minhas_preferencias():
    filtro = {"username": get_jwt_identity(), "ativo": {"$ne": False}}
    projecao = {"_id": 0, "preferencias": 1}
    try:
        usuarios = conectar_db()["tbl_usuarios"]
        if request.method == 'PATCH':
            dados = request.get_json(silent=True)
            if not validar_preferencias(dados):
                return jsonify({"erro": "Envie somente preferências de aparência válidas."}), 400
            usuario = usuarios.find_one_and_update(
                filtro, {"$set": {f"preferencias.{chave}": valor for chave, valor in dados.items()}},
                projection=projecao, return_document=ReturnDocument.AFTER,
            )
        else:
            usuario = usuarios.find_one(filtro, projecao)
        if usuario is None:
            return jsonify({"erro": "Usuário não encontrado ou desativado."}), 401
        return jsonify({"preferencias": preferencias_usuario(usuario)}), 200
    except PyMongoError:
        return jsonify({"erro": "Não foi possível acessar suas preferências. Tente novamente."}), 503


@app.route('/usuarios',methods=['GET'])
def lista_users():
    tipo_perfil = request.args.get("tipo_perfil")

    filtro = {"papel":"usuario", "ativo": {"$ne": False}}

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
        {"username": username,"papel":"usuario", "ativo": {"$ne": False}},
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

    if dados_user.get("papel") == "admin" and username == get_jwt_identity():
        return jsonify({"erro": "Não é permitido desativar sua própria conta administrativa"}), 400
    usuario = conectar_db()["tbl_usuarios"].update_one(
        {"username": username},
        {"$set": {"ativo": False}},
    )

    if usuario.matched_count == 0:
        return jsonify({"erro": "Usuário não encontrado"}), 404
    return jsonify(), 204


def email_valido(email):
    if not isinstance(email,str):
        return False

    return re.fullmatch(
        r"[^@\s]+@[^@\s]+\.[^@\s]+",
        email.strip()
    ) is not None

def validar_localizacao(dados):
    for campo in ("cidade", "estado"):
        if campo not in dados:
            continue
        valor = dados[campo]
        if not isinstance(valor, str) or len(valor.strip()) > 100:
            return campo
        dados[campo] = valor.strip()
    return None


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

    campo_invalido = validar_localizacao(novo_usuario)
    if campo_invalido:
        return jsonify({"erro": f"{campo_invalido} deve ser um texto de até 100 caracteres"}), 400

    if "papel" in novo_usuario:
        return jsonify({"erro":"Papel é definido pelo servidor"}),400

    if any(campo in {"pagamento", *CAMPOS_LEGADOS} or campo.startswith("_paddle_")
           for campo in novo_usuario):
        return jsonify({"erro": "Os dados da assinatura são definidos pelo servidor."}), 400

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
    novo_usuario["ativo"] = True
    
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

    campo_invalido = validar_localizacao(dados)
    if campo_invalido:
        return jsonify({"erro": f"{campo_invalido} deve ser um texto de até 100 caracteres"}), 422

    campos_editaveis = {
        "nome", "email", "contato", "tipo_perfil",
        "nicho", "plataformas", "descricao", "cidade", "estado",
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

@app.route("/usuarios/me/assinatura/checkout", methods=["POST"])
@jwt_required()
def criar_checkout_assinatura():
    dados = request.get_json(silent=True)
    if request.data and dados != {}:
        return jsonify({"erro": "O checkout não aceita preço, usuário ou validade enviados pelo cliente."}), 400
    username = get_jwt_identity()
    db = conectar_db()
    usuario = db["tbl_usuarios"].find_one({"username": username}, {"_id": 0})
    if usuario is None:
        return jsonify({"erro": "Usuário não encontrado"}), 401
    if usuario.get("papel") != "usuario" or usuario.get("tipo_perfil") not in TIPO_PERFIL:
        return jsonify({"erro": "Checkout disponível somente para empresas e influenciadores."}), 403
    if assinatura_premium_ativa(usuario):
        return jsonify({"erro": "Sua assinatura Premium já está ativa."}), 409

    checkouts = db["tbl_checkout_pagamentos"]
    referencia = str(uuid4())
    reservado = False
    try:
        _, token, preco_id = configuracao_paddle()
        existente = checkouts.find_one({"_id": username})
        if existente:
            transacao_id = existente.get("transaction_id")
            if not transacao_id:
                return jsonify({"erro": "Há um checkout em preparação ou aguardando conferência. Não crie outra assinatura."}), 409
            if not re.fullmatch(r"txn_[a-z0-9]{26}", transacao_id):
                raise ErroPagamento("O registro do checkout precisa ser conferido.")
            transacao = requisitar_paddle("GET", f"/transactions/{transacao_id}")
            validar_transacao(transacao, username, preco_id)
            if transacao.get("status") in ("draft", "ready"):
                return jsonify({"environment": "sandbox", "transaction_id": transacao_id,
                                "client_token": token, "reused": True}), 200
            pode_reiniciar = transacao.get("status") == "canceled"
            if transacao.get("status") == "completed" and transacao.get("subscription_id"):
                assinatura_id = transacao["subscription_id"]
                if not re.fullmatch(r"sub_[a-z0-9]{26}", assinatura_id):
                    raise ErroPagamento("Identificador de assinatura inválido.")
                assinatura = requisitar_paddle("GET", f"/subscriptions/{assinatura_id}")
                pode_reiniciar = assinatura.get("id") == assinatura_id and assinatura.get("status") == "canceled"
            if not pode_reiniciar:
                return jsonify({"erro": "O checkout já foi processado. Aguarde a confirmação do pagamento ou confira sua assinatura."}), 409
            checkouts.delete_one({"_id": username, "transaction_id": transacao_id})

        validar_preco(preco_id)
        try:
            checkouts.insert_one({"_id": username, "reference": referencia,
                                  "status": "creating", "environment": "sandbox",
                                  "price_id": preco_id, "created_at": datetime.now(timezone.utc)})
        except DuplicateKeyError:
            return jsonify({"erro": "Um checkout já está sendo preparado. Tente novamente em instantes."}), 409
        reservado = True
        transacao = requisitar_paddle("POST", "/transactions", {
            "items": [{"price_id": preco_id, "quantity": 1}],
            "collection_mode": "automatic", "currency_code": "BRL",
            "custom_data": {"username": username, "checkout_reference": referencia},
        })
        # Salve o ID antes de outras validações para permitir conferência sem duplicar compras.
        transacao_id = transacao.get("id")
        checkouts.update_one({"_id": username, "reference": referencia},
                             {"$set": {"transaction_id": transacao_id, "status": "pending"}})
        reservado = False
        validar_transacao(transacao, username, preco_id)
        if transacao.get("status") not in ("draft", "ready"):
            raise ErroPagamento("O checkout de teste não está disponível para abertura.")
        return jsonify({"environment": "sandbox", "transaction_id": transacao_id,
                        "client_token": token, "reused": False}), 201
    except ErroPagamento as erro:
        if reservado:
            filtro = {"_id": username, "reference": referencia}
            if erro.resultado_incerto:
                checkouts.update_one(filtro, {"$set": {"status": "needs_review"}})
            else:
                checkouts.delete_one(filtro)
        return jsonify({"erro": str(erro)}), erro.status


@app.route("/webhooks/pagamentos", methods=["POST"])
def receber_webhook_pagamentos():
    try:
        evento = verificar_evento(request.get_data(cache=True), request.headers.get("Paddle-Signature"))
        resultado = processar_evento(conectar_db(), evento)
        return jsonify({"status": resultado}), 200
    except ErroPagamento as erro:
        return jsonify({"erro": str(erro)}), erro.status
    except PyMongoError:
        return jsonify({"erro": "Não foi possível registrar o evento. O provedor pode reenviá-lo."}), 503


@app.route("/usuarios/me/assinatura/cancelamento", methods=["POST"])
@jwt_required()
def cancelar_assinatura_paddle():
    if request.data and request.get_json(silent=True) != {}:
        return jsonify({"erro": "O cancelamento usa somente a identidade do usuário autenticado."}), 400
    try:
        usuario = cancelar_renovacao(conectar_db(), get_jwt_identity())
        return jsonify({"mensagem": "Renovação cancelada. O acesso permanece até o fim do período pago.",
                        **campos_assinatura(usuario)}), 200
    except ErroPagamento as erro:
        return jsonify({"erro": str(erro)}), erro.status
    except PyMongoError:
        return jsonify({"erro": "Não foi possível salvar o cancelamento. Tente novamente para conferir o estado."}), 503


@app.route("/usuarios/<string:username>/assinatura", methods=["PATCH"])
@jwt_required()
def alterar_assinatura(username):
    if get_jwt_identity() != username:
        return jsonify({"erro": "Acesso Negado"}), 403
    return jsonify({"erro": "Ativação manual removida. Use o checkout e a confirmação de pagamento pelo provedor."}), 410

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

    if usuario.get("ativo", True) is False:
        return jsonify({"erro": "Conta desativada. Entre em contato com a administração."}), 403

    token = create_access_token(identity=usuario["username"],
                                additional_claims={"email":usuario["email"]})
    return jsonify(access_token = token),200

def assinatura_premium_ativa(usuario, hoje=None):
    if "pagamento" in usuario or "premium_ate" in usuario:
        return premium_ativo(usuario)
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
        {"_id": 0, "username": 1, "pagamento": 1, "assinatura": 1, "expiracao_assinatura": 1, "premium_ate": 1},
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

def buscar_influenciador_avaliacoes(db, username):
    usuario = db["tbl_usuarios"].find_one(
        {"username": username, "ativo": {"$ne": False}},
        {"_id": 0, "username": 1, "tipo_perfil": 1},
    )
    if usuario is None or usuario.get("tipo_perfil") != "influenciador":
        return None
    return usuario


_avaliacoes_indice_lock = Lock()
_avaliacoes_indice_pronto = False


def garantir_indice_avaliacoes(colecao):
    global _avaliacoes_indice_pronto
    if _avaliacoes_indice_pronto:
        return
    with _avaliacoes_indice_lock:
        if _avaliacoes_indice_pronto:
            return
        colecao.create_index(
            [("avaliador_username", 1), ("alvo_username", 1)],
            unique=True,
            name="uniq_avaliacoes_avaliador_alvo",
            partialFilterExpression={
                "avaliador_username": {"$type": "string"},
                "alvo_username": {"$type": "string"},
            },
        )
        _avaliacoes_indice_pronto = True


@app.route("/usuarios/<string:username>/avaliacoes", methods=["GET"])
def consultar_resumo_avaliacoes(username):
    db = conectar_db()
    if buscar_influenciador_avaliacoes(db, username) is None:
        return jsonify({"erro": "Influenciador não encontrado"}), 404

    resumo = next(iter(db["tbl_avaliacoes"].aggregate([
        {"$match": {"alvo_username": username}},
        {"$group": {
            "_id": None,
            "nota_media": {"$avg": "$nota"},
            "quantidade_avaliacoes": {"$sum": 1},
        }},
    ])), None)
    return jsonify({
        "nota_media": round(resumo["nota_media"], 2) if resumo else None,
        "quantidade_avaliacoes": resumo["quantidade_avaliacoes"] if resumo else 0,
    }), 200


@app.route("/usuarios/<string:username>/avaliacoes/comentarios", methods=["GET"])
@jwt_required()
def consultar_comentarios_avaliacoes(username):
    db = conectar_db()
    identidade = get_jwt_identity()
    solicitante = db["tbl_usuarios"].find_one(
        {"username": identidade, "ativo": {"$ne": False}},
        {"_id": 0, "papel": 1, "tipo_perfil": 1, "pagamento": 1,
         "assinatura": 1, "expiracao_assinatura": 1, "premium_ate": 1},
    )
    if solicitante is None:
        return jsonify({"erro": "Usuário não encontrado"}), 401

    eh_admin = solicitante.get("papel") == "admin"
    eh_empresa_premium = (
        solicitante.get("tipo_perfil") == "empresa"
        and assinatura_premium_ativa(solicitante)
    )
    if not eh_admin and not eh_empresa_premium:
        return jsonify({"erro": "Acesso restrito a empresas Premium e administradores"}), 403

    if buscar_influenciador_avaliacoes(db, username) is None:
        return jsonify({"erro": "Influenciador não encontrado"}), 404

    registros = db["tbl_avaliacoes"].find(
        {"alvo_username": username},
        {"_id": 0, "avaliador_username": 1, "nota": 1, "conteudo": 1, "criado_em": 1},
    ).sort("criado_em", -1)
    comentarios = [{
        "avaliador_username": item.get("avaliador_username"),
        "nota": item.get("nota"),
        "conteudo": item.get("conteudo"),
        "criado_em": item.get("criado_em"),
    } for item in registros]
    return jsonify({"avaliacoes": comentarios}), 200


@app.route("/usuarios/<string:username>/avaliacoes", methods=["POST"])
@jwt_required()
def avaliar(username):
    db = conectar_db()
    identidade = get_jwt_identity()
    avaliador = db["tbl_usuarios"].find_one(
        {"username": identidade, "ativo": {"$ne": False}},
        {"_id": 0, "username": 1, "tipo_perfil": 1},
    )
    if avaliador is None:
        return jsonify({"erro": "Usuário não encontrado"}), 401
    if avaliador.get("tipo_perfil") != "empresa":
        return jsonify({"erro": "Somente empresas podem avaliar influenciadores"}), 403

    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify({"erro": "JSON inválido"}), 400

    campos_obrigatorios = ["nota", "conteudo"]
    campos_faltantes = [campo for campo in campos_obrigatorios if campo not in dados]
    if campos_faltantes:
        return jsonify({
            "erro": "Campos obrigatórios ausentes",
            "campos_faltantes": campos_faltantes,
        }), 400
    if type(dados["nota"]) is not int or not 1 <= dados["nota"] <= 5:
        return jsonify({"erro": "nota deve ser um inteiro entre 1 e 5"}), 422
    if not isinstance(dados["conteudo"], str) or not dados["conteudo"].strip():
        return jsonify({"erro": "Valor inválido para conteudo"}), 422
    conteudo = dados["conteudo"].strip()
    if len(conteudo) > 2000:
        return jsonify({"erro": "conteudo deve ter no máximo 2000 caracteres"}), 422

    influenciador = buscar_influenciador_avaliacoes(db, username)
    if influenciador is None:
        return jsonify({"erro": "Influenciador não encontrado"}), 404
    if identidade == username:
        return jsonify({"erro": "Não é permitido avaliar o próprio perfil"}), 403

    colecao = db["tbl_avaliacoes"]
    try:
        garantir_indice_avaliacoes(colecao)
        if colecao.find_one({
            "avaliador_username": identidade,
            "alvo_username": username,
        }):
            return jsonify({"erro": "Esta empresa já avaliou este influenciador"}), 409

        avaliacao = {
            "avaliador_username": identidade,
            "alvo_username": username,
            "nota": dados["nota"],
            "conteudo": conteudo,
            "criado_em": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        colecao.insert_one(avaliacao)
    except DuplicateKeyError:
        return jsonify({"erro": "Esta empresa já avaliou este influenciador"}), 409

    return jsonify({
        "mensagem": "Avaliação feita com sucesso",
        "avaliacao": avaliacao,
    }), 201

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

@app.route("/admin/usuarios/<string:username>/status", methods=["PATCH"])
@admin_required
def alterar_status_usuario(username):
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict) or set(dados) != {"ativo", "senha"} or type(dados.get("ativo")) is not bool:
        return jsonify({"erro": "Informe ativo como true ou false e sua senha atual"}), 400
    senha = dados.get("senha")
    if not isinstance(senha, str) or not senha:
        return jsonify({"erro": "Informe sua senha atual"}), 400
    colecao = conectar_db()["tbl_usuarios"]
    administrador = colecao.find_one({"username": get_jwt_identity()})
    if not bcrypt.check_password_hash(administrador["senha"], senha):
        return jsonify({"erro": "Senha inválida"}), 400
    if not dados["ativo"] and username == get_jwt_identity():
        return jsonify({"erro": "Não é permitido desativar sua própria conta administrativa"}), 400
    resultado = colecao.update_one({"username": username}, {"$set": {"ativo": dados["ativo"]}})
    if resultado.matched_count == 0:
        return jsonify({"erro": "Usuário não encontrado"}), 404
    return jsonify({"username": username, "ativo": dados["ativo"]}), 200


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
    novo_admin["ativo"] = True

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



# Denúncias: identidade e permissões são sempre determinadas pelo servidor.
MOTIVOS_DENUNCIA = {
    "Perfil falso ou falsa identidade", "Fraude ou tentativa de golpe",
    "Assédio ou comportamento ofensivo", "Conteúdo inadequado", "Spam", "Outro motivo",
}
STATUS_DENUNCIA = {"pendente", "em_analise", "concluida"}


def serializar_denuncia(documento):
    campos = ("denunciante", "denunciado", "motivo", "descricao", "status",
              "resposta", "criada_em", "atualizada_em", "administrador")
    resultado = {campo: documento.get(campo) for campo in campos}
    resultado["id"] = str(documento["_id"])
    for campo in ("criada_em", "atualizada_em"):
        data = resultado[campo]
        if isinstance(data, datetime):
            if data.tzinfo is None:
                data = data.replace(tzinfo=timezone.utc)
            resultado[campo] = data.astimezone(timezone.utc).isoformat()
    return resultado


def denunciante_required(func):
    @wraps(func)
    @jwt_required()
    def wrapper(*args, **kwargs):
        usuario = conectar_db()["tbl_usuarios"].find_one({"username": get_jwt_identity()})
        if usuario is None:
            return jsonify({"erro": "Usuário não encontrado"}), 401
        if usuario.get("papel") != "usuario" or usuario.get("tipo_perfil") not in TIPO_PERFIL:
            return jsonify({"erro": "Acesso negado"}), 403
        return func(*args, **kwargs)
    return wrapper


@app.route("/denuncias", methods=["POST"])
@denunciante_required
def criar_denuncia():
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify({"erro": "Envie um objeto JSON válido"}), 400
    if set(dados) - {"denunciado", "motivo", "descricao"}:
        return jsonify({"erro": "Envie apenas denunciado, motivo e descricao"}), 400
    if any(not isinstance(dados.get(campo), str) for campo in ("denunciado", "motivo", "descricao")):
        return jsonify({"erro": "Informe perfil, motivo e descrição"}), 400
    denunciado, motivo, descricao = (dados[campo].strip() for campo in ("denunciado", "motivo", "descricao"))
    if not denunciado or len(denunciado) > 200 or motivo not in MOTIVOS_DENUNCIA or not 20 <= len(descricao) <= 2000:
        return jsonify({"erro": "Informe um perfil, um motivo válido e uma descrição de 20 a 2000 caracteres"}), 400
    username = get_jwt_identity()
    if denunciado == username:
        return jsonify({"erro": "Você não pode denunciar a própria conta"}), 400
    db = conectar_db()
    alvo = db["tbl_usuarios"].find_one({"username": denunciado, "papel": "usuario"})
    if alvo is None or alvo.get("tipo_perfil") not in TIPO_PERFIL:
        return jsonify({"erro": "Perfil denunciado não encontrado"}), 404
    agora = datetime.now(timezone.utc)
    documento = {"_id": ObjectId(), "denunciante": username, "denunciado": denunciado,
                 "motivo": motivo, "descricao": descricao, "status": "pendente",
                 "resposta": "", "criada_em": agora, "atualizada_em": agora, "administrador": None}
    db["tbl_denuncias"].insert_one(documento)
    return jsonify({"denuncia": serializar_denuncia(documento)}), 201


@app.route("/denuncias", methods=["GET"])
@denunciante_required
def minhas_denuncias():
    documentos = conectar_db()["tbl_denuncias"].find({"denunciante": get_jwt_identity()}).sort("criada_em", -1)
    return jsonify([serializar_denuncia(item) for item in documentos]), 200


@app.route("/admin/denuncias", methods=["GET"])
@admin_required
def listar_denuncias_admin():
    status = request.args.get("status")
    if status is not None and status not in STATUS_DENUNCIA:
        return jsonify({"erro": "Status inválido"}), 400
    filtro = {"status": status} if status else {}
    documentos = conectar_db()["tbl_denuncias"].find(filtro).sort("criada_em", -1)
    return jsonify([serializar_denuncia(item) for item in documentos]), 200


@app.route("/admin/denuncias/<string:denuncia_id>", methods=["PATCH"])
@admin_required
def atualizar_denuncia_admin(denuncia_id):
    if not ObjectId.is_valid(denuncia_id):
        return jsonify({"erro": "Denúncia não encontrada"}), 404
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict) or not dados or set(dados) - {"status", "resposta"}:
        return jsonify({"erro": "Envie status e/ou resposta"}), 400
    if "status" in dados and (not isinstance(dados["status"], str) or dados["status"] not in STATUS_DENUNCIA):
        return jsonify({"erro": "Status inválido"}), 400
    if "resposta" in dados and (not isinstance(dados["resposta"], str) or len(dados["resposta"].strip()) > 2000):
        return jsonify({"erro": "A resposta deve ser um texto de até 2000 caracteres"}), 400
    colecao = conectar_db()["tbl_denuncias"]
    documento = colecao.find_one({"_id": ObjectId(denuncia_id)})
    if documento is None:
        return jsonify({"erro": "Denúncia não encontrada"}), 404
    status = dados.get("status", documento["status"])
    resposta = dados.get("resposta", documento.get("resposta", "")).strip()
    if status == "concluida" and not resposta:
        return jsonify({"erro": "Informe uma resposta para concluir a denúncia"}), 400
    alteracoes = {"status": status, "resposta": resposta,
                  "administrador": get_jwt_identity(), "atualizada_em": datetime.now(timezone.utc)}
    # Impede que duas análises simultâneas sobrescrevam silenciosamente uma à outra.
    resultado = colecao.update_one({"_id": documento["_id"], "atualizada_em": documento["atualizada_em"]}, {"$set": alteracoes})
    if not resultado.matched_count:
        return jsonify({"erro": "A denúncia foi alterada. Atualize a lista e tente novamente"}), 409
    documento.update(alteracoes)
    return jsonify({"denuncia": serializar_denuncia(documento)}), 200
def identificar_canal_youtube(url):
    """Converte formatos comuns de URL de canal nos filtros da YouTube API."""
    if not isinstance(url, str) or not url.strip():
        return None

    valor = url.strip()
    if "://" not in valor:
        valor = "https://" + valor

    try:
        parsed = urlparse(valor)
    except ValueError:
        return None

    host = (parsed.hostname or "").lower()
    if host not in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        return None

    partes = [parte for parte in parsed.path.split("/") if parte]
    if not partes:
        return None

    if partes[0] == "channel" and len(partes) >= 2:
        return {"id": partes[1]}
    if partes[0].startswith("@") and len(partes[0]) > 1:
        return {"forHandle": partes[0]}
    if partes[0] == "user" and len(partes) >= 2:
        return {"forUsername": partes[1]}
    return None


def consultar_youtube(endpoint, parametros):
    chave = os.getenv("YOUTUBE_API_KEY")
    if not chave:
        return None, (jsonify({
            "erro": "Integração com YouTube não configurada",
            "detalhe": "Defina a variável de ambiente YOUTUBE_API_KEY",
        }), 503)

    try:
        resposta = requests.get(
            f"{YOUTUBE_API_URL}/{endpoint}",
            params={**parametros, "key": chave},
            timeout=10,
        )
        resposta.raise_for_status()
        return resposta.json(), None
    except requests.HTTPError as erro:
        status = erro.response.status_code if erro.response is not None else None
        if status in {403, 429}:
            return None, (jsonify({
                "erro": "Cota ou acesso da YouTube Data API indisponível"
            }), 503)
        return None, (jsonify({"erro": "Falha ao consultar a YouTube Data API"}), 502)
    except requests.RequestException:
        return None, (jsonify({"erro": "Não foi possível consultar o YouTube"}), 502)
    except ValueError:
        return None, (jsonify({"erro": "Resposta inválida recebida do YouTube"}), 502)


def converter_metrica_publica(valor):
    return int(valor) if valor is not None else None


def obter_estatisticas_publicas_youtube(identificador):
    canais, erro = consultar_youtube("channels", {
        "part": "snippet,statistics,contentDetails",
        **identificador,
    })
    if erro:
        return None, erro

    itens_canais = canais.get("items", [])
    if not itens_canais:
        return None, (jsonify({"erro": "Canal do YouTube não encontrado"}), 404)

    canal = itens_canais[0]
    snippet = canal.get("snippet", {})
    estatisticas = canal.get("statistics", {})
    playlist_uploads = (
        canal.get("contentDetails", {})
        .get("relatedPlaylists", {})
        .get("uploads")
    )

    videos_recentes = []
    if playlist_uploads:
        playlist, erro = consultar_youtube("playlistItems", {
            "part": "contentDetails",
            "playlistId": playlist_uploads,
            "maxResults": 10,
        })
        if erro:
            return None, erro

        ids_videos = [
            item.get("contentDetails", {}).get("videoId")
            for item in playlist.get("items", [])
        ]
        ids_videos = [video_id for video_id in ids_videos if video_id]
        if ids_videos:
            videos, erro = consultar_youtube("videos", {
                "part": "snippet,statistics",
                "id": ",".join(ids_videos),
            })
            if erro:
                return None, erro

            videos_recentes = [{
                "id": video["id"],
                "titulo": video.get("snippet", {}).get("title"),
                "publicado_em": video.get("snippet", {}).get("publishedAt"),
                "visualizacoes": converter_metrica_publica(
                    video.get("statistics", {}).get("viewCount")
                ),
                "curtidas": converter_metrica_publica(
                    video.get("statistics", {}).get("likeCount")
                ),
                "comentarios": converter_metrica_publica(
                    video.get("statistics", {}).get("commentCount")
                ),
                "url": f"https://www.youtube.com/watch?v={video['id']}",
            } for video in videos.get("items", [])]

    inscricoes_ocultas = estatisticas.get("hiddenSubscriberCount", False)
    return {
        "plataforma": "youtube",
        "perfil": {
            "id": canal.get("id"),
            "nome": snippet.get("title"),
            "descricao": snippet.get("description"),
            "url": f"https://www.youtube.com/channel/{canal.get('id')}",
            "criado_em": snippet.get("publishedAt"),
        },
        "metricas": {
            "seguidores": None if inscricoes_ocultas else converter_metrica_publica(
                estatisticas.get("subscriberCount")
            ),
            "seguidores_ocultos": inscricoes_ocultas,
            "visualizacoes_totais": converter_metrica_publica(
                estatisticas.get("viewCount")
            ),
            "total_publicacoes": converter_metrica_publica(
                estatisticas.get("videoCount")
            ),
        },
        "videos_recentes": videos_recentes,
    }, None


@app.route("/analises/youtube", methods=["POST"])
@jwt_required()
def criar_analise_youtube():
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify({"erro": "JSON inválido"}), 400

    url = dados.get("url")
    identificador = identificar_canal_youtube(url)
    if identificador is None:
        return jsonify({
            "erro": "Link de canal do YouTube inválido ou não suportado",
            "formatos_suportados": [
                "youtube.com/@canal",
                "youtube.com/channel/ID_DO_CANAL",
                "youtube.com/user/usuario",
            ],
        }), 400

    usuario = conectar_db()["tbl_usuarios"].find_one(
        {"username": get_jwt_identity()},
        {"_id": 0, "tipo_perfil": 1},
    )
    if usuario is None:
        return jsonify({"erro": "Usuário não encontrado"}), 401
    if usuario.get("tipo_perfil") != "influenciador":
        return jsonify({"erro": "Somente influenciadores podem cadastrar uma análise"}), 403

    analise, erro = obter_estatisticas_publicas_youtube(identificador)
    if erro:
        return erro

    agora = datetime.now(timezone.utc)
    username = get_jwt_identity()
    documento = {
        **analise,
        "username": username,
        "data_referencia": agora.date().isoformat(),
        "coletado_em": agora,
    }
    colecao = conectar_db()["tbl_analises_influenciadores"]
    colecao.update_one(
        {
            "username": username,
            "plataforma": "youtube",
            "perfil.id": analise["perfil"]["id"],
            "data_referencia": documento["data_referencia"],
        },
        {"$set": documento},
        upsert=True,
    )

    documento["coletado_em"] = agora.isoformat()
    return jsonify({"analise": documento}), 200


@app.route("/analises/youtube", methods=["GET"])
@jwt_required()
def listar_historico_analises_youtube():
    return responder_historico_youtube(get_jwt_identity())


@app.route("/analises/youtube/<string:username>", methods=["GET"])
@jwt_required()
def listar_historico_youtube_por_influenciador(username):
    usuarios = conectar_db()["tbl_usuarios"]
    solicitante = usuarios.find_one(
        {"username": get_jwt_identity()}, {"_id": 0, "tipo_perfil": 1}
    )
    if solicitante is None:
        return jsonify({"erro": "Usuário não encontrado"}), 401
    if solicitante.get("tipo_perfil") != "empresa" and not (
        solicitante.get("tipo_perfil") == "influenciador"
        and get_jwt_identity() == username
    ):
        return jsonify({"erro": "Acesso negado"}), 403

    influenciador = usuarios.find_one(
        {"username": username}, {"_id": 0, "tipo_perfil": 1}
    )
    if influenciador is None or influenciador.get("tipo_perfil") != "influenciador":
        return jsonify({"erro": "Influenciador não encontrado"}), 404
    return responder_historico_youtube(username)


def responder_historico_youtube(username):
    documentos = list(
        conectar_db()["tbl_analises_influenciadores"].find(
            {"username": username, "plataforma": "youtube"},
            {"_id": 0, "username": 0},
        ).sort("data_referencia", -1).limit(90)
    )
    documentos.reverse()
    for documento in documentos:
        coletado_em = documento.get("coletado_em")
        if isinstance(coletado_em, datetime):
            documento["coletado_em"] = coletado_em.isoformat()

    return jsonify({
        "plataforma": "youtube",
        "historico": documentos,
    }), 200


from contratos import registrar_contratos
from conversas import registrar_conversas
registrar_contratos(app, lambda: conectar_db())
registrar_conversas(app, lambda: conectar_db())

if __name__ == '__main__':
    app.run(debug=True)
