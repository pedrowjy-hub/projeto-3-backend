"""Preferências privadas de aparência; nenhum campo de conta é editável por aqui."""
PADRAO = {"tema": "claro", "fonte": "padrao", "cor": "verde", "reduzirMovimento": False, "altoContraste": False}
OPCOES = {"tema": {"claro", "escuro", "sistema"}, "fonte": {"padrao", "grande", "maior"}, "cor": {"verde", "azul", "ameixa"}}


def validar_preferencias(dados):
    if not isinstance(dados, dict) or not dados or set(dados) - PADRAO.keys():
        return False
    for chave, valor in dados.items():
        if chave in OPCOES:
            if not isinstance(valor, str) or valor not in OPCOES[chave]:
                return False
        elif type(valor) is not bool:
            return False
    return True


def preferencias_usuario(usuario):
    valores = usuario.get("preferencias")
    resultado = dict(PADRAO)
    if isinstance(valores, dict):
        for chave in PADRAO:
            if chave in valores and validar_preferencias({chave: valores[chave]}):
                resultado[chave] = valores[chave]
    return resultado
