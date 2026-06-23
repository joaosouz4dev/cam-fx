"""Gera os assets do CamFX a partir da logo: remove o fundo branco (transparente)
e exporta logo.png (varios tamanhos) e icon.ico (multi-resolucao).

Uso: python assets/make_assets.py <logo_origem.png>
"""
import sys
from pathlib import Path
import numpy as np
from PIL import Image
from collections import deque

HERE = Path(__file__).resolve().parent
src = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "logo_src.png"

im = Image.open(src).convert("RGBA")
arr = np.array(im)
h, w = arr.shape[:2]

# Flood fill a partir das bordas: torna transparente o branco conectado ao
# exterior (nao fura os brancos internos, como os olhos/webcam).
rgb = arr[:, :, :3].astype(np.int16)
is_white = (rgb[:, :, 0] > 235) & (rgb[:, :, 1] > 235) & (rgb[:, :, 2] > 235)
visited = np.zeros((h, w), dtype=bool)
q = deque()
for x in range(w):
    for y in (0, h - 1):
        if is_white[y, x] and not visited[y, x]:
            visited[y, x] = True; q.append((y, x))
for y in range(h):
    for x in (0, w - 1):
        if is_white[y, x] and not visited[y, x]:
            visited[y, x] = True; q.append((y, x))
while q:
    y, x = q.popleft()
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        ny, nx = y + dy, x + dx
        if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx] and is_white[ny, nx]:
            visited[ny, nx] = True; q.append((ny, nx))

arr[visited, 3] = 0  # fundo externo -> transparente
out = Image.fromarray(arr)

# Recorta para o conteudo (bounding box do alpha) e deixa quadrado.
bbox = out.getbbox()
out = out.crop(bbox)
side = max(out.size)
sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
sq.paste(out, ((side - out.width) // 2, (side - out.height) // 2))
out = sq

# logo.png principal (512) e a versao grande para o README.
out.resize((512, 512), Image.LANCZOS).save(HERE / "logo.png")
out.resize((256, 256), Image.LANCZOS).save(HERE / "logo_256.png")

# icon.ico multi-resolucao (app/janela/bandeja/instalador).
sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
out.save(HERE / "icon.ico", sizes=sizes)

print("Gerado:", HERE / "logo.png", HERE / "icon.ico")
