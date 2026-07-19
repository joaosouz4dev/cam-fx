"""Simulacao da concorrencia do Pipeline - SEM camera, SEM GPU, SEM build.

O bug da Fase 3 e de THREADING (start/stop/restart/demand loop + _loop
competindo), nao de camera/GPU. Aqui trocamos as partes lentas (open_camera,
SwapStage, CamFXVirtualCamera, blur, framing) por FAKES rapidos com delays
configuraveis que imitam a lentidao real (abrir camera ~2-25s, motor ~6-30s).

Instrumentamos o _loop para contar quantas threads _loop rodam AO MESMO TEMPO
(deve ser SEMPRE <=1) e detectar deadlock (o pipeline nunca chega a processar).

Roda centenas de ciclos de start/stop/restart concorrente em segundos, expondo
corridas e deadlocks que so apareceriam no app real. Uso:
    python tools/sim_pipeline_concurrency.py
"""

import sys
import threading
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402


# ---- contadores globais de diagnostico ----
class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.loop_active = 0        # threads _loop rodando o corpo agora
        self.loop_max = 0           # pico simultaneo (deve ser <=1)
        self.frames_sent = 0        # frames enviados a camera virtual fake
        self.cam_open_conflicts = 0  # 2 caps abertos ao mesmo tempo
        self.cams_open = 0
        self.cam_opens_total = 0    # aberturas ACUMULADAS (hot_toggle: deve ser 1)

    def enter_loop(self):
        with self.lock:
            self.loop_active += 1
            self.loop_max = max(self.loop_max, self.loop_active)

    def exit_loop(self):
        with self.lock:
            self.loop_active -= 1

    def open_cam(self):
        with self.lock:
            self.cams_open += 1
            self.cam_opens_total += 1
            if self.cams_open > 1:
                self.cam_open_conflicts += 1

    def close_cam(self):
        with self.lock:
            self.cams_open = max(0, self.cams_open - 1)


STATS = Stats()

# delays que imitam a lentidao real (segundos) - reduzidos p/ a sim ser rapida
CAM_OPEN_DELAY = 0.3      # abrir camera (real ~2-25s)
MOTOR_LOAD_DELAY = 0.5    # carregar motor DLC (real ~6-30s)

# Instrumentacao do cenario hot_toggle (limpo a cada run_sim). A prova
# principal e medida DENTRO do FakeSwapStage.prepare: quantos frames a camera
# enviou ENQUANTO o motor "carregava" - imune a jitter de scheduling.
HOT = {}


class FakeCap:
    """Camera fake: entrega frames pretos, conta abertura/fechamento."""
    def __init__(self):
        STATS.open_cam()
        self._released = False

    def read(self):
        return True, np.zeros((720, 1280, 3), dtype=np.uint8)

    def wait_warmed(self, timeout=4.0):
        return True

    def release(self):
        if not self._released:
            self._released = True
            STATS.close_cam()


def fake_open_camera(index, width=None, height=None, fps=None):
    time.sleep(CAM_OPEN_DELAY)   # imita a lentidao de abrir a webcam
    return FakeCap(), "DirectShow"


class FakeSwapStage:
    """SwapStage fake: prepare() demora (motor), process() devolve o frame."""
    def __init__(self, *a, **k):
        self.ready = False

    def prepare(self):
        HOT["prepare_started"] = True
        f0 = STATS.frames_sent
        time.sleep(MOTOR_LOAD_DELAY)   # imita carregar o motor DLC
        # frames que a camera enviou ENQUANTO o motor carregava (startup
        # progressivo: deve ser > 0; no fluxo antigo era sempre 0)
        HOT["frames_during_load"] = STATS.frames_sent - f0
        self.ready = True
        HOT["prepare_finished"] = True
        return True

    def process(self, frame):
        HOT["swap_processed"] = HOT.get("swap_processed", 0) + 1
        return frame

    def close(self):
        self.ready = False


class FakeVCam:
    """Camera virtual fake: conta frames enviados."""
    def __init__(self, *a, **k):
        self.device = "CamFX"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def send(self, frame):
        with STATS.lock:
            STATS.frames_sent += 1


class FakeEffect:
    """blur/framing fake: passa o frame direto."""
    def __init__(self, *a, **k):
        self.active_provider = "fake"

    def process(self, frame, *a, **k):
        return frame

    def close(self):
        pass


def run_sim(scenario: str, seconds: float = 8.0) -> dict:
    """Roda um cenario e devolve as stats. Cada cenario exercita start/stop/
    restart de um jeito diferente para expor corridas."""
    global STATS
    STATS = Stats()
    HOT.clear()

    import camfx.pipeline as P
    from camfx.config import Config

    # instrumenta o _loop_body para contar concorrencia
    orig_body = P.Pipeline._loop_body

    def counted_body(self):
        STATS.enter_loop()
        try:
            orig_body(self)
        finally:
            STATS.exit_loop()

    patches = [
        mock.patch.object(P.Pipeline, "_loop_body", counted_body),
        mock.patch.object(P, "open_camera", fake_open_camera),
        mock.patch.object(P, "CamFXVirtualCamera", FakeVCam),
        mock.patch.object(P, "BackgroundBlur", FakeEffect),
        mock.patch.object(P, "AutoFraming", FakeEffect),
    ]
    # SwapStage e importado dentro do _loop (lazy); faz o patch no modulo dele
    import camfx.faceswap.swap_stage as SS
    patches.append(mock.patch.object(SS, "SwapStage", FakeSwapStage))

    for p in patches:
        p.start()
    try:
        cfg = Config.load()
        cfg.faceswap_enabled = True
        pipe = P.Pipeline(cfg)
        # _use_bridge le config real; forca True para exercitar o SwapStage
        pipe._use_bridge = lambda: True

        stop_demand = threading.Event()

        def demand():
            # imita o webui._demand_loop: liga se want e nao running.
            # Re-checa stop_demand IMEDIATAMENTE antes de start() para nao criar
            # um pipeline zumbi no teardown (o main seta stop_demand + stop()
            # em paralelo; sem esta 2a checagem, um start() tardio ressuscitava
            # o pipeline e ele atravessava para o proximo cenario).
            while not stop_demand.is_set():
                if not getattr(pipe, "_restarting", False):
                    if not pipe.running and not stop_demand.is_set():
                        pipe.start()
                time.sleep(0.1)   # agressivo (real: 1s)

        t0 = time.time()
        if scenario == "demand_only":
            threading.Thread(target=demand, daemon=True).start()
        elif scenario == "start_stop_spam":
            def spam():
                while time.time() - t0 < seconds:
                    pipe.start(); time.sleep(0.05)
                    pipe.stop(); time.sleep(0.05)
            for _ in range(3):
                threading.Thread(target=spam, daemon=True).start()
        elif scenario == "restart_spam":
            threading.Thread(target=demand, daemon=True).start()
            def rspam():
                while time.time() - t0 < seconds:
                    time.sleep(0.3)
                    pipe.restart()
            for _ in range(2):
                threading.Thread(target=rspam, daemon=True).start()
        elif scenario == "toggle_swap":
            # Fluxo LEGADO (restart manual, hoje usado so para trocar camera/
            # device): toggles via config + restart. Mantido como regressao do
            # proprio restart; o fluxo real de toggle de efeito e o hot_toggle.
            threading.Thread(target=demand, daemon=True).start()
            time.sleep(2)   # deixa subir com swap
            def toggler():
                on = True
                while time.time() - t0 < seconds:
                    time.sleep(2.0)   # usuario clica a cada ~2s (realista)
                    on = not on
                    pipe._use_bridge = (lambda v: (lambda: v))(on)
                    pipe.restart()
            threading.Thread(target=toggler, daemon=True).start()
        elif scenario == "hot_toggle":
            # Fluxo NOVO (startup progressivo + toggle quente): camera fluindo
            # com swap OFF; liga o swap SEM restart (apply_effects) e PROVA que
            # os frames continuam fluindo DURANTE o load do motor; desliga e
            # prova que e instantaneo. Camera aberta UMA unica vez no cenario.
            swap_on = [False]
            pipe._use_bridge = lambda: swap_on[0]
            threading.Thread(target=demand, daemon=True).start()

            def hot_test():
                # 1) baseline: frames fluindo com swap OFF
                t_end = time.time() + 5
                while STATS.frames_sent < 10 and time.time() < t_end:
                    time.sleep(0.05)
                if STATS.frames_sent < 10:
                    HOT["why"] = "sem frames no baseline"; return
                # 2) liga SEM restart (o caminho real da UI: apply_effects)
                swap_on[0] = True
                pipe.apply_effects()
                t_end = time.time() + 3
                while not HOT.get("prepare_started") and time.time() < t_end:
                    time.sleep(0.02)
                if not HOT.get("prepare_started"):
                    HOT["why"] = "loader nao iniciou o prepare"; return
                # 3) prova primaria ja medida DENTRO do prepare (fake dormindo)
                t_end = time.time() + 5
                while not HOT.get("prepare_finished") and time.time() < t_end:
                    time.sleep(0.02)
                if HOT.get("frames_during_load", 0) < 5:
                    HOT["why"] = ("frames pararam durante o load "
                                  f"({HOT.get('frames_during_load', 0)})")
                    return
                # 4) plugou ao vivo: frames passando pelo estagio. Poll com
                # deadline (nao sleep fixo): o plug acontece so depois de o
                # loader disputar o _lock, e no runner do CI (2 cores, churn de
                # np.zeros por frame) isso pode atrasar - um check unico apos
                # sleep fixo flakava. Aqui esperamos ate ~3s pelo 1o process.
                t_end = time.time() + 3
                while HOT.get("swap_processed", 0) <= 0 and time.time() < t_end:
                    time.sleep(0.02)
                if HOT.get("swap_processed", 0) <= 0:
                    HOT["why"] = "swap nao plugou ao vivo"; return
                # 5) desliga: instantaneo, sem gap nos frames enviados
                fc = STATS.frames_sent
                swap_on[0] = False
                pipe.config.faceswap_enabled = False
                pipe.apply_effects()
                sp1 = HOT.get("swap_processed", 0)
                time.sleep(0.2)
                if STATS.frames_sent <= fc:
                    HOT["why"] = "frames pararam apos o toggle-off"; return
                time.sleep(0.3)
                if HOT.get("swap_processed", 0) != sp1:
                    HOT["why"] = "swap continuou processando apos o off"; return
                HOT["ok"] = True

            threading.Thread(target=hot_test, daemon=True).start()

        time.sleep(seconds)
        # No restart_spam, para o spam mas deixa o demand loop rodar mais um
        # pouco: o teste e se o pipeline ESTABILIZA e processa DEPOIS do estresse
        # (no app real o usuario nao da restart a cada 0.3s pra sempre).
        settle_frames_before = STATS.frames_sent
        if scenario in ("restart_spam", "toggle_swap"):
            time.sleep(3.0)   # deixa estabilizar apos o estresse
        stop_demand.set()
        healthy = pipe.running and pipe._thread is not None \
            and pipe._thread.is_alive()
        pipe.stop()
        time.sleep(0.5)
        return {
            "loop_max": STATS.loop_max,
            "cam_conflicts": STATS.cam_open_conflicts,
            "frames_sent": STATS.frames_sent,
            "frames_after_settle": STATS.frames_sent - settle_frames_before,
            "cams_still_open": STATS.cams_open,
            "cam_opens_total": STATS.cam_opens_total,
            "healthy_before_stop": healthy,
            "hot_ok": HOT.get("ok", False),
            "hot_why": HOT.get("why", ""),
            "frames_during_load": HOT.get("frames_during_load", 0),
        }
    finally:
        # Espera os stage-loaders morrerem ANTES de remover os patches: um
        # loader tardio (stale, dormindo no prepare fake) importaria o
        # SwapStage REAL depois do unpatch - lento e fora do controle da sim.
        t_end = time.time() + 5
        while time.time() < t_end and any(
                t.name == "camfx-stage-loader" and t.is_alive()
                for t in threading.enumerate()):
            time.sleep(0.05)
        for p in patches:
            p.stop()


def main():
    scenarios = ["demand_only", "start_stop_spam", "restart_spam",
                 "toggle_swap", "hot_toggle"]
    ok = True
    for sc in scenarios:
        r = run_sim(sc, seconds=8.0)
        # criterios: nunca 2 _loop simultaneos, sem conflito de camera,
        # camera nao fica aberta no fim, e (p/ demand/restart) chegou a enviar
        # frames (processou).
        max_ok = r["loop_max"] <= 1
        cam_ok = r["cam_conflicts"] == 0 and r["cams_still_open"] == 0
        if sc == "start_stop_spam":
            processed = True   # so liga/desliga, nao espera processar
        elif sc in ("restart_spam", "toggle_swap"):
            # apos o estresse, deve estar saudavel E processar (estabilizou)
            processed = r["healthy_before_stop"] and r["frames_after_settle"] > 0
        elif sc == "hot_toggle":
            # startup progressivo: frames DURANTE o load do motor, toggle
            # instantaneo, e a camera aberta UMA unica vez (nunca reaberta)
            processed = (r["healthy_before_stop"] and r["hot_ok"]
                         and r["cam_opens_total"] == 1)
        else:
            processed = r["frames_sent"] > 0
        good = max_ok and cam_ok and processed
        ok = ok and good
        flag = "OK " if good else "FALHOU"
        print(f"[{flag}] {sc}: loop_max={r['loop_max']} "
              f"cam_conflicts={r['cam_conflicts']} "
              f"cams_open_fim={r['cams_still_open']} "
              f"frames={r['frames_sent']} "
              f"apos_estresse={r.get('frames_after_settle','-')}")
        if sc == "hot_toggle":
            print(f"        hot: frames_durante_load={r['frames_during_load']} "
                  f"aberturas_camera={r['cam_opens_total']} "
                  f"{('motivo: ' + r['hot_why']) if r['hot_why'] else ''}")
        if not max_ok:
            print(f"        -> DUAS threads _loop simultaneas (corrida!)")
        if r["cam_conflicts"]:
            print(f"        -> camera aberta 2x ao mesmo tempo")
        if r["cams_still_open"]:
            print(f"        -> camera VAZOU (nao liberou no stop)")
        if not processed:
            print(f"        -> nao estabilizou/processou apos o estresse")

    print()
    print(">>> SIMULACAO OK <<<" if ok else ">>> SIMULACAO ACHOU PROBLEMA <<<")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
