"""Trava a selecao do modelo de face swap configurado.

Regressao coberta: o app ficava preso em "Verificando modelos de IA..." porque
o prepare sempre exigia `inswapper_128.onnx`, mesmo quando a config selecionava
`inswapper_128_fp16.onnx` ja baixado.

Rodar: python tools/test_faceswap_model_selection.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from camfx.models import _faceswap_model_spec


def check(desc, cond):
    print(f"  [{'OK' if cond else 'FALHOU'}] {desc}")
    assert cond, desc


def main():
    name, url, dest = _faceswap_model_spec(
        fp16=False,
        swap_model_id="inswapper_128_fp16",
    )
    check("config fp16 resolve o arquivo fp16",
          name == "inswapper_128_fp16.onnx")
    check("config fp16 usa URL do catalogo", "models-3.0.0" in url)
    check("destino fp16 fica no cache CamFX", dest.name == name)

    name, _url, _dest = _faceswap_model_spec(fp16=False)
    check("sem config explicita continua no modelo padrao",
          name == "inswapper_128.onnx")

    with tempfile.TemporaryDirectory() as tmp:
        custom = Path(tmp) / "custom.onnx"
        custom.write_bytes(b"x")
        name, url, dest = _faceswap_model_spec(
            swap_model_id="custom",
            swap_model_path=str(custom),
        )
        check("modelo custom usa o arquivo escolhido", dest == custom)
        check("modelo custom nao tem URL para download", url is None)

    print("\n>>> SELECAO DE MODELO DE FACE SWAP OK <<<")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
