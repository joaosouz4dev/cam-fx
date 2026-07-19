"""Regressao dos bugs de STALENESS do stage-loader (achados da revisao
adversarial do startup progressivo).

Dois bugs HIGH, ambos variantes do bug historico "loader pluga estagio de
config VELHA": um estagio caro (SwapStage) carrega em background; enquanto ele
carrega, algo muda (foto/modelo trocados, ou o loop morre). O loader NAO pode
plugar o estagio obsoleto. Aqui reproduzimos as duas corridas com fakes rapidos
(sem camera/GPU/motor), controlando o timing com um Event que segura o "prepare".

Rodar: python tools/test_stage_loader_staleness.py
"""
import sys
import threading
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402


def check(desc, cond):
    print(f"  [{'OK' if cond else 'FALHOU'}] {desc}")
    assert cond, desc


# ---- fakes ----
class FakeCap:
    def read(self):
        return True, np.zeros((8, 8, 3), dtype=np.uint8)

    def wait_warmed(self, timeout=4.0):
        return True

    def release(self):
        pass


def fake_open_camera(index, width=None, height=None, fps=None):
    return FakeCap(), "DirectShow"


class FakeVCam:
    device = "CamFX"

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def send(self, frame):
        pass


class FakeEffect:
    active_provider = "fake"

    def __init__(self, *a, **k):
        pass

    def process(self, frame, *a, **k):
        return frame

    def close(self):
        pass


# SwapStage fake cujo prepare() bloqueia num gate controlado pelo teste, para
# criar a janela "carregando" onde a corrida acontece. Registra com que
# source_path foi CONSTRUIDO (e como isso identifica config velha vs nova).
class GatedSwapStage:
    gate = threading.Event()
    built = []          # source_paths na ordem de construcao
    plugged_paths = []  # source_paths que chegaram a ser plugados (ready)

    def __init__(self, source_path, **k):
        self._sp = source_path
        self.ready = False
        GatedSwapStage.built.append(source_path)

    def prepare(self):
        GatedSwapStage.gate.wait(timeout=10)  # segura o "load" ate o teste soltar
        self.ready = True
        return True

    def process(self, frame):
        return frame

    def close(self):
        self.ready = False


def _patched(pipe_mod, swap_mod):
    return [
        mock.patch.object(pipe_mod, "open_camera", fake_open_camera),
        mock.patch.object(pipe_mod, "CamFXVirtualCamera", FakeVCam),
        mock.patch.object(pipe_mod, "BackgroundBlur", FakeEffect),
        mock.patch.object(pipe_mod, "AutoFraming", FakeEffect),
        mock.patch.object(swap_mod, "SwapStage", GatedSwapStage),
    ]


def _wait(cond, timeout=5):
    t_end = time.time() + timeout
    while not cond() and time.time() < t_end:
        time.sleep(0.02)
    return cond()


def test_reload_during_load():
    """BUG 1: trocar a foto ENQUANTO o swap carrega nao pode deixar a foto
    VELHA no ar. O loader que carrega 'old.jpg' deve descartar (stale por gen)
    e um novo loader deve plugar 'new.jpg'."""
    import camfx.pipeline as P
    import camfx.faceswap.swap_stage as SS
    from camfx.config import Config

    GatedSwapStage.gate.clear()
    GatedSwapStage.built.clear()
    GatedSwapStage.plugged_paths.clear()

    patches = _patched(P, SS)
    for p in patches:
        p.start()
    try:
        cfg = Config.load()
        cfg.faceswap_enabled = True
        cfg.blur_enabled = False
        cfg.framing_enabled = False
        cfg.source_face_path = "old.jpg"
        pipe = P.Pipeline(cfg)
        pipe._use_bridge = lambda: bool(pipe.config.source_face_path)

        pipe.start()
        # espera o loader construir o SwapStage com a foto VELHA e entrar no
        # prepare (gate segurando)
        check("loader construiu o swap com old.jpg",
              _wait(lambda: "old.jpg" in GatedSwapStage.built))
        # troca a foto no meio do load (o caminho real: config + apply_effects)
        pipe.config.source_face_path = "new.jpg"
        pipe.apply_effects(reload_swap=True)
        # solta o gate: os DOIS loaders (old e new) destravam
        GatedSwapStage.gate.set()
        # o swap plugado tem de ser o NOVO; espera estabilizar
        _wait(lambda: pipe._swap is not None and pipe._swap.ready, timeout=5)
        plugged = getattr(pipe._swap, "_sp", None)
        check(f"o swap plugado usa a foto NOVA (plugado={plugged!r})",
              plugged == "new.jpg")
        check("new.jpg chegou a ser construido (loader novo rodou)",
              "new.jpg" in GatedSwapStage.built)
        pipe.stop()
    finally:
        GatedSwapStage.gate.set()
        for p in patches:
            p.stop()


def test_no_plug_after_loop_death():
    """BUG 2: se o loop morre (camera cai) enquanto o swap carrega, o loader
    NAO pode plugar o estagio num pipeline morto (vazaria no proximo start).
    Simulamos a morte parando o pipeline (stop invalida run_token+gen); o
    estagio construido antes NAO deve acabar plugado."""
    import camfx.pipeline as P
    import camfx.faceswap.swap_stage as SS
    from camfx.config import Config

    GatedSwapStage.gate.clear()
    GatedSwapStage.built.clear()

    patches = _patched(P, SS)
    for p in patches:
        p.start()
    try:
        cfg = Config.load()
        cfg.faceswap_enabled = True
        cfg.blur_enabled = False
        cfg.framing_enabled = False
        cfg.source_face_path = "old.jpg"
        pipe = P.Pipeline(cfg)
        pipe._use_bridge = lambda: bool(pipe.config.source_face_path)

        pipe.start()
        check("loader entrou no prepare (construiu o swap)",
              _wait(lambda: len(GatedSwapStage.built) >= 1))
        # a run morre ANTES de o swap terminar de carregar
        pipe.stop()
        # agora solta o gate: o loader tardio acorda e tenta plugar
        GatedSwapStage.gate.set()
        time.sleep(0.5)
        check("o loader tardio NAO plugou o swap numa run morta",
              pipe._swap is None)
    finally:
        GatedSwapStage.gate.set()
        for p in patches:
            p.stop()


def main():
    print("BUG 1 - reload de foto durante o load do swap:")
    test_reload_during_load()
    print("BUG 2 - loop morre durante o load do swap:")
    test_no_plug_after_loop_death()
    print("\n>>> STALENESS DO STAGE-LOADER OK <<<")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
