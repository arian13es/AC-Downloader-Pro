; AC-Downloader Pro — Windows installer script (Inno Setup 6)
; Build: ISCC.exe installer\AC-Downloader.iss
; Output goes to installer\output\

#define MyAppName "AC-Downloader Pro"
#define MyAppVersion "1.0.0"
#define MyAppExeName "AC-Downloader.exe"

[Setup]
AppId={{7E1F9C42-3B8A-4D5E-9C21-A6D84F0B2AC1}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
DefaultDirName={localappdata}\Programs\AC-Downloader
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=AC-Downloader-Setup-{#MyAppVersion}-win64
SetupIconFile=app.ico
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
WizardStyle=modern
MinVersion=10.0
; Close a running instance before install/uninstall so files are never left
; locked (locked files create PendingFileRenameOperations -> "must restart" errors)
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
LaunchApp=Run %1 after finishing installation

[Files]
; onedir bundle: the whole application folder (exe + _internal runtime)
Source: "..\dist\AC-Downloader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Explicit IconFilename forces Windows to bind OUR icon (not a cached fallback)
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchApp,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; User recordings live in Documents\AC-Downloader — never touched.
Type: filesandordirs; Name: "{localappdata}\Temp\acdownloader"

[Code]
// Kill any running instance before install / uninstall so nothing stays locked.
procedure KillRunningInstance();
var
  Res: Integer;
begin
  Exec(ExpandConstant('{cmd}'),
       '/C taskkill /F /IM AC-Downloader.exe >nul 2>&1',
       '', SW_HIDE, ewWaitUntilTerminated, Res);
  Sleep(500);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';            // empty = proceed
  KillRunningInstance();
end;

function InitializeUninstall(): Boolean;
begin
  KillRunningInstance();
  Result := True;
end;
