; Inno Setup script for M7 Studio Editor (per-user install, no admin rights needed).
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{7F3C2A9E-6D1B-4E8A-9C55-0D2B7A1E4F70}
AppName=M7 Studio Editor
AppVersion={#AppVersion}
AppPublisher=M7 Studio
DefaultDirName={localappdata}\Programs\M7 Studio
DefaultGroupName=M7 Studio Editor
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=..\..\dist-installer
OutputBaseFilename=M7-Studio-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=M7 Studio Editor
LicenseFile=..\..\LICENSE

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\..\dist-app\M7 Studio\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\M7 Studio Editor"; Filename: "{app}\M7 Studio.exe"; IconFilename: "{app}\engine\RapidRAW.exe"
Name: "{group}\Uninstall M7 Studio Editor"; Filename: "{uninstallexe}"
Name: "{userdesktop}\M7 Studio Editor"; Filename: "{app}\M7 Studio.exe"; IconFilename: "{app}\engine\RapidRAW.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\M7 Studio.exe"; Description: "Start M7 Studio Editor now"; Flags: nowait postinstall skipifsilent
