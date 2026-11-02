import base64
import binascii
import warnings
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError


def normalizar_foto(valor):
    if valor == "":
        return ""
    if not isinstance(valor, str) or len(valor) > 500_000:
        raise ValueError("Foto inválida ou muito grande. Selecione outra imagem.")
    prefixo, separador, conteudo = valor.partition(",")
    if not separador or prefixo not in {"data:image/jpeg;base64", "data:image/png;base64", "data:image/webp;base64"}:
        raise ValueError("A foto deve ser JPG, PNG ou WebP.")
    try:
        dados = base64.b64decode(conteudo, validate=True)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(dados)) as imagem:
                if imagem.format not in {"JPEG", "PNG", "WEBP"} or imagem.width * imagem.height > 4_000_000:
                    raise ValueError("Dimensões da foto inválidas.")
                imagem.load()
                foto = ImageOps.fit(ImageOps.exif_transpose(imagem).convert("RGB"), (512, 512))
                saida = BytesIO()
                foto.save(saida, format="JPEG", quality=85)
        return "data:image/jpeg;base64," + base64.b64encode(saida.getvalue()).decode("ascii")
    except (binascii.Error, OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as erro:
        raise ValueError("Arquivo de imagem inválido. Escolha outra foto.") from erro
