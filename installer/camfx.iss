; Instalador do CamFX (Inno Setup)
; Gera CamFX-Setup.exe que instala o app, o driver de camera virtual Media
; Foundation e o helper, registra o driver e cria atalhos.
;
; Compilar: ISCC.exe installer\camfx.iss
; Requer que os binarios estejam em dist\ e nas pastas do driver/helper:
;   dist\CamFX.exe                     (app, gerado por build.py)
;   dist\VCamSampleSource.dll          (source MF)
;   dist\camfx_vcam.exe                (host da camera virtual)

#define AppName "CamFX"
; Versao pode vir do CI via: ISCC /DMyAppVersion=0.0.1 ...
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define AppVersion MyAppVersion
#define AppPublisher "Joao Victor Souza"
#define AppExe "CamFX.exe"

[Setup]
AppId={{C8F3A1D2-CA3F-4E77-9B21-CAMFX0000001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\CamFX
DefaultGroupName=CamFX
DisableProgramGroupPage=yes
OutputDir=.\output
OutputBaseFilename=CamFX-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
; Precisa de admin para registrar o driver (regsvr32 em HKLM) e escrever em Program Files.
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Auto-atualizacao: fecha o CamFX em execucao antes de copiar os arquivos.
; AppMutex bate com o mutex de instancia unica do app, para o Inno detectar o
; CamFX rodando mesmo no modo silencioso e fecha-lo.
; RestartApplications=no de proposito: o Restart Manager do Windows tentaria
; reabrir o app durante a copia (cedo demais), causando duas instancias e o
; erro "Failed to load Python DLL". Quem reabre o app, uma unica vez e so no
; fim, e a entrada [Run] abaixo.
CloseApplications=yes
RestartApplications=no
AppMutex=CamFX_SingleInstance_Mutex

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na area de trabalho"; GroupDescription: "Atalhos:"
Name: "startup"; Description: "Iniciar o CamFX com o Windows (minimizado)"; GroupDescription: "Inicializacao:"; Flags: unchecked

[Files]
; Bundle onedir do PyInstaller: copia a pasta dist\CamFX inteira (exe + DLLs +
; dados + _internal) para {app}, recursivamente. O DLL da camera virtual e
; registrado via regserver; o helper acompanha. Todos vem de dist\CamFX porque
; o build.py (onedir) coloca o exe e os componentes la dentro.
Source: "..\dist\CamFX\VCamSampleSource.dll"; DestDir: "{app}"; Flags: ignoreversion regserver 64bit
Source: "..\dist\CamFX\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "VCamSampleSource.dll"

[Dirs]
; Pasta de dados compartilhada (frame.bin) acessivel a todas as sessoes/contas,
; para o Frame Server (Local Service) ler os frames do app.
Name: "{commonappdata}\CamFX"; Permissions: everyone-full

[Icons]
Name: "{group}\CamFX";            Filename: "{app}\{#AppExe}"
Name: "{group}\Desinstalar CamFX"; Filename: "{uninstallexe}"
Name: "{commondesktop}\CamFX";    Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Inicio com o Windows (minimizado), se o usuario marcar a task.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "CamFX"; ValueData: """{app}\{#AppExe}"" --minimized"; \
  Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "Abrir o CamFX agora"; Flags: nowait postinstall skipifsilent
; Em atualizacao silenciosa (auto-update do app), reabre o CamFX minimizado.
Filename: "{app}\{#AppExe}"; Parameters: "--minimized"; Flags: nowait runasoriginaluser; Check: WizardSilent

[UninstallRun]
; Encerra o host antes de desinstalar para liberar o DLL/arquivos.
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM camfx_vcam.exe"; Flags: runhidden; RunOnceId: "killvcam"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM CamFX.exe";      Flags: runhidden; RunOnceId: "killapp"
