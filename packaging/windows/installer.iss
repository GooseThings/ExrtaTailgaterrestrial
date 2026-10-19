; installer.iss - Inno Setup script for the Windows installer. Built by
; build.ps1 (next to this file), which passes the version in:
;
;   ISCC.exe /DAppVersion=1.2.0 packaging\windows\installer.iss
;
; Installs the PyInstaller build in dist\ExtraTailgaterrestrial\ per user by
; default (no admin rights needed; the setup offers an all-users install
; too), with a Start-menu shortcut, an optional desktop icon and an
; uninstaller. The app keeps its own data in %LOCALAPPDATA%\ExtraTailgaterrestrial,
; which uninstalling leaves alone.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; Never change AppId - Windows uses it to recognise upgrades of this app.
AppId={{3E51FDE1-40C5-4CA6-92B8-E7735794EEB3}
AppName=ExtraTailgaterrestrial
AppVersion={#AppVersion}
AppPublisher=GooseThings
AppPublisherURL=https://github.com/GooseThings/ExrtaTailgaterrestrial
AppSupportURL=https://github.com/GooseThings/ExrtaTailgaterrestrial/issues
DefaultDirName={autopf}\ExtraTailgaterrestrial
DefaultGroupName=ExtraTailgaterrestrial
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist
OutputBaseFilename=ExtraTailgaterrestrial-{#AppVersion}-setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\ExtraTailgaterrestrial.exe
WizardStyle=modern
Compression=lzma2
SolidCompression=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\..\dist\ExtraTailgaterrestrial\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\ExtraTailgaterrestrial"; Filename: "{app}\ExtraTailgaterrestrial.exe"
Name: "{autodesktop}\ExtraTailgaterrestrial"; Filename: "{app}\ExtraTailgaterrestrial.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ExtraTailgaterrestrial.exe"; Description: "{cm:LaunchProgram,ExtraTailgaterrestrial}"; Flags: nowait postinstall skipifsilent
