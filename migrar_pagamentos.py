"""Organiza assinaturas antigas. Sem --aplicar, apenas conta os registros elegíveis."""
import argparse
from datetime import datetime, timedelta, timezone

from assinaturas import (
    CAMPOS_LEGADOS, dados_pagamento, data_utc, iso, premium_ativo,
    reservar_usuario, liberar_usuario,
)


def converter_pagamento(usuario):
    if "pagamento" in usuario:
        raise ValueError("Registro já convertido.")
    pagamento = dados_pagamento(usuario)
    if pagamento["status"] is None:
        pagamento["status"] = "active" if usuario.get("assinatura") is True else None
    if "premium_ate" not in usuario and usuario.get("assinatura") is True:
        try:
            ultimo_dia = datetime.strptime(usuario.get("expiracao_assinatura", ""), "%d-%m-%Y")
            fim = ultimo_dia.replace(tzinfo=timezone(timedelta(hours=-3))) + timedelta(days=1) - timedelta(microseconds=1)
            pagamento["premium_ate"] = iso(fim)
        except (ValueError, TypeError):
            pagamento["premium_ate"] = None
    # Uma data inválida não ganha validade durante a conversão.
    if not data_utc(pagamento["premium_ate"]):
        pagamento["premium_ate"] = None
    if premium_ativo(usuario) != premium_ativo({"pagamento": pagamento}):
        raise ValueError("Registro inconsistente; migração não pode alterar o acesso.")
    return pagamento


def migrar_usuario(usuarios, username):
    token = reservar_usuario(usuarios, username)
    try:
        usuario = usuarios.find_one({"username": username})
        if "pagamento" in usuario:
            return False
        pagamento = converter_pagamento(usuario)
        resultado = usuarios.update_one(
            {"username": username, "_paddle_lock_token": token, "pagamento": {"$exists": False}},
            {"$set": {"pagamento": pagamento}, "$unset": {campo: "" for campo in CAMPOS_LEGADOS}},
        )
        if resultado.matched_count != 1:
            raise ValueError("Registro alterado durante a migração; tente novamente.")
        return True
    finally:
        liberar_usuario(usuarios, username, token)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aplicar", action="store_true", help="Grava a conversão no banco configurado em .env.")
    args = parser.parse_args()
    from api import conectar_db
    from pagamentos import ErroPagamento
    from pymongo.errors import PyMongoError

    try:
        usuarios = conectar_db()["tbl_usuarios"]
        consulta = {"pagamento": {"$exists": False}, "$or": [{campo: {"$exists": True}} for campo in CAMPOS_LEGADOS]}
        projecao = {"username": 1, **{campo: 1 for campo in CAMPOS_LEGADOS}}
        elegiveis = convertidos = pendentes = 0
        for usuario in usuarios.find(consulta, projecao):
            try:
                converter_pagamento(usuario)
                elegiveis += 1
                if args.aplicar and migrar_usuario(usuarios, usuario["username"]):
                    convertidos += 1
            except (ValueError, ErroPagamento, KeyError):
                pendentes += 1
        print(f"Elegiveis: {elegiveis}; convertidos: {convertidos}; pendentes de conferencia/reenvio: {pendentes}.")
        if not args.aplicar:
            print("Simulacao: nenhum registro foi alterado. Use --aplicar para gravar.")
        return 1 if pendentes else 0
    except PyMongoError:
        print("Nao foi possivel acessar ou atualizar o banco. Confira a conexao e execute novamente.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
