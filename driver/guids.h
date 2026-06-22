// CLSID unico do source filter CamFX. Gerado especificamente para este projeto;
// nao reutilizar em outro filtro para nao colidir no registro do Windows.
#pragma once
#include <initguid.h>

// {C8BE3141-67B6-47F3-B7C4-1F57CBA5B470}
DEFINE_GUID(CLSID_CamFXSource,
    0xc8be3141, 0x67b6, 0x47f3, 0xb7, 0xc4, 0x1f, 0x57, 0xcb, 0xa5, 0xb4, 0x70);

// Nome amigavel exibido na lista de cameras dos aplicativos.
#define CAMFX_FILTER_NAME L"CamFX"
