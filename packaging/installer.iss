; Desk Sentry per-user installer. Generated values come from build_release.py.
#ifndef GeneratedDefines
  #error GeneratedDefines must point to installer-defines.iss
#endif
#include GeneratedDefines

[Setup]
AppId={{7EAFD7CD-908C-4F1B-AB4B-8CB50A948B59}
AppName=Desk Sentry
AppVersion={#MyAppVersion}
AppVerName=Desk Sentry {#MyAppVersion}
AppPublisher=Desk Sentry contributors
AppPublisherURL={#MyRepositoryUrl}
AppSupportURL={#MyRepositoryUrl}/issues
AppUpdatesURL={#MyRepositoryUrl}/releases/latest
VersionInfoVersion={#MyAppVersion}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoDescription=Desk Sentry Setup
DefaultDirName={localappdata}\Programs\DeskSentry
DisableDirPage=yes
UsePreviousAppDir=no
DefaultGroupName=Desk Sentry
PrivilegesRequired=lowest
AppMutex=DeskSentry.SingleInstance
; pythonw.exe may also belong to unrelated applications. Graceful shutdown is
; requested through Desk Sentry's own IPC below, then guarded by AppMutex.
CloseApplications=no
RestartApplications=no
OutputDir={#MyOutputDir}
OutputBaseFilename=DeskSentry-{#MyAppVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=desk_sentry.ico
UninstallDisplayIcon={app}\desk_sentry.ico

[InstallDelete]
; A release replaces any developer deployment; drop its commit marker so
; tools/deploy.ps1 does not mistake this install for the checked-out HEAD.
Type: files; Name: "{app}\VERSION.txt"
; Remove only the obsolete PyInstaller files from releases before 2.0.1.
Type: files; Name: "{app}\DeskSentry.exe"
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#MySourceDir}\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Excludes: "THIRD_PARTY_NOTICES.md"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "startup"; Description: "Start Desk Sentry when I sign in"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "DeskSentry"; ValueData: """{app}\python\pythonw.exe"" -m desk_sentry --minimized"; Tasks: startup
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "DeskSentry"; Check: StartupNotSelected; Flags: deletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "DeskSentry"; Flags: uninsdeletevalue

[Icons]
Name: "{userprograms}\Desk Sentry"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m desk_sentry"; WorkingDir: "{app}"; IconFilename: "{app}\desk_sentry.ico"

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-m desk_sentry --minimized"; WorkingDir: "{app}"; Description: "Start Desk Sentry"; Flags: nowait postinstall skipifsilent

[Code]
const
  AppMutexName = 'DeskSentry.SingleInstance';

function InstalledExecutable: String;
begin
  Result := ExpandConstant('{localappdata}\Programs\DeskSentry\python\pythonw.exe');
  if not FileExists(Result) then
    Result := ExpandConstant('{localappdata}\Programs\DeskSentry\DeskSentry.exe');
end;

procedure RequestGracefulQuit;
var
  Executable: String;
  ResultCode: Integer;
  Waited: Integer;
begin
  Executable := InstalledExecutable;
  if FileExists(Executable) then
  begin
    Log('Requesting graceful Desk Sentry shutdown before file replacement.');
    if CompareText(ExtractFileName(Executable), 'pythonw.exe') = 0 then
    begin
      if not Exec(Executable, '-m desk_sentry --quit',
        ExpandConstant('{localappdata}\Programs\DeskSentry'), SW_HIDE,
        ewWaitUntilTerminated, ResultCode) then
        Log('Could not request Desk Sentry shutdown: ' + SysErrorMessage(ResultCode));
    end
    else if not Exec(Executable, '--quit', ExtractFileDir(Executable), SW_HIDE,
      ewWaitUntilTerminated, ResultCode) then
      Log('Could not request Desk Sentry shutdown: ' + SysErrorMessage(ResultCode));
  end;

  { Inno Pascal Script has no GetTickCount: count 100 ms polls, up to 10 s. }
  Waited := 0;
  while CheckForMutexes('DeskSentry.SingleInstance') and (Waited < 100) do
  begin
    Sleep(100);
    Waited := Waited + 1;
  end;
end;

function InitializeSetup: Boolean;
begin
  { Inno executes InitializeSetup before its AppMutex check. This gives the
    running app time to flush diagnostics and release the mutex. }
  RequestGracefulQuit;
  Result := True;
end;

function StartupNotSelected: Boolean;
begin
  Result := not WizardIsTaskSelected('startup');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usAppMutexCheck then
    RequestGracefulQuit;
  if (CurUninstallStep = usPostUninstall) and (not UninstallSilent) then
    if MsgBox('Also delete Desk Sentry settings and diagnostics?',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(ExpandConstant('{userappdata}\DeskSentry'), True, True, True);
end;
