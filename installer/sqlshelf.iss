; Inno Setup script for SQLShelf
; Build: pyinstaller sqlshelf.spec  (generates dist\SQLShelf\)
; Then:  iscc /DAppVersion=X.Y.Z installer\sqlshelf.iss
;        (CI passes /DAppVersion from the git tag; local builds fall back to the default below)

#define AppName      "SQLShelf"
#ifndef AppVersion
  #define AppVersion "1.0.4"
#endif
#define AppPublisher "Raphael Franco"
#define AppURL       "https://github.com/raphamaster/sqlshelf"
#define AppExeName   "SQLShelf.exe"
#define DistDir      "..\dist\SQLShelf"

[Setup]
AppId={{A3F2C1D4-7E8B-4F9A-B0C5-D6E3F2A1B8C9}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=SQLShelf-{#AppVersion}-windows-x64-setup
SetupIconFile=..\images\FavIcon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}";       Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
