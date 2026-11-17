; Inno Setup script for QuickLauncher.
; Build with:  python scripts/build_installer.py   (or make_installer.bat)
; The version is passed in by the build script (/DAppVersion=x.y.z).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "QuickLauncher"
#define AppExe  "QuickLauncher.exe"

[Setup]
; Never change this GUID: it is how upgrades find the previous installation.
AppId={{BC715117-C75A-4299-823E-2936999CF40B}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Kayaniu-xD and xLordTime
AppPublisherURL=https://github.com/Kayaniu-xD/QuickLauncher
AppSupportURL=https://github.com/Kayaniu-xD/QuickLauncher/issues
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
SetupIconFile=..\icon.ico
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=QuickLauncher-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Default: install for all users into Program Files (asks for admin).
; The install dialog also offers "only for me" (no admin, %LOCALAPPDATA%\Programs).
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
; Close a running launcher instead of failing on the locked exe when upgrading.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "german";  MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Only the program itself. User data (config, accounts, logs) is NEVER shipped:
; it is created on first start inside {app}\data and {app}\logs.
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\icon.ico";       DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE";        DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md";      DestDir: "{app}"; Flags: ignoreversion

[Dirs]
; Everything the launcher writes lives in these two folders inside the install
; folder. They are the only writable parts, so normal users can use the program
; from Program Files while the .exe itself stays protected.
Name: "{app}\data"; Permissions: users-modify
Name: "{app}\logs"; Permissions: users-modify

[Icons]
Name: "{group}\{#AppName}";            Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{group}\Uninstall {#AppName}";  Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";      Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[Code]
// On uninstall, ask whether the user's accounts / settings / logs should go too.
// Default is NO: a reinstall then picks up exactly where the user left off.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if (not UninstallSilent) and
       (MsgBox('Do you also want to delete your saved accounts, settings and logs?' + #13#10 +
               '(Folder: ' + ExpandConstant('{app}') + ')' + #13#10#13#10 +
               'Choose "No" to keep them for a later reinstall.',
               mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES) then
    begin
      DelTree(ExpandConstant('{app}\data'), True, True, True);
      DelTree(ExpandConstant('{app}\logs'), True, True, True);
      RemoveDir(ExpandConstant('{app}'));
    end;
  end;
end;
