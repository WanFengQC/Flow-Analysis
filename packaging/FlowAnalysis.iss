; Flow Analysis 的 per-user 安装包。安装路径位于 LocalAppData，因此自动更新
; 不需要管理员权限，也不会覆盖用户配置和运行数据目录。

#ifndef MyAppVersion
  #error "必须通过 /DMyAppVersion=x.y.z 传入发布版本"
#endif

#define MyAppName "Flow Analysis"
#define MyAppPublisher "Flow Analysis"
#define MyAppExeName "FlowAnalysis.exe"

[Setup]
AppId={{9D414BB7-B5CC-492D-9DDE-4B377C9C99F4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=FlowAnalysisSetup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UninstallDisplayName={#MyAppName}

[Files]
Source: "..\dist\pyinstaller\FlowAnalysis\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标："; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
