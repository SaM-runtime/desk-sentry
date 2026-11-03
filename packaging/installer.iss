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
CloseApplications=yes
CloseApplicationsFilter=DeskSentry.exe
RestartApplications=no
OutputDir={#MyOutputDir}
OutputBaseFilename=DeskSentry-{#MyAppVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=desk_sentry.ico
UninstallDisplayIcon={app}\DeskSentry.exe

[InstallDelete]
; A release replaces any developer deployment; drop its commit marker so
; tools/deploy.ps1 does not mistake this install for the checked-out HEAD.
Type: files; Name: "{app}\VERSION.txt"

[Files]
Source: "{#MySourceDir}\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Excludes: "THIRD_PARTY_NOTICES.md"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "startup"; Description: "Start Desk Sentry when I sign in"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "DeskSentry"; ValueData: """{app}\DeskSentry.exe"" --minimized"; Tasks: startup
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "DeskSentry"; Check: StartupNotSelected; Flags: deletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "DeskSentry"; Flags: uninsdeletevalue

[Icons]
Name: "{userprograms}\Desk Sentry"; Filename: "{app}\DeskSentry.exe"

[Run]
Filename: "{app}\DeskSentry.exe"; Parameters: "--minimized"; Description: "Start Desk Sentry"; Flags: nowait postinstall skipifsilent

[Code]
const
  AppMutexName = 'DeskSentry.SingleInstance';

function InstalledExecutable: String;
begin
  Result := ExpandConstant('{localappdata}\Programs\DeskSentry\DeskSentry.exe');
end;

procedure RequestGracefulQuit;
var
  Executable: String;
  ResultCode: Integer;
  StartedAt: Cardinal;
begin
  Executable := InstalledExecutable;
  if FileExists(Executable) then
  begin
    Log('Requesting graceful Desk Sentry shutdown before file replacement.');
    if not Exec(Executable, '--quit', ExtractFileDir(Executable), SW_HIDE,
      ewWaitUntilTerminated, ResultCode) then
      Log('Could not launch DeskSentry.exe --quit: ' + SysErrorMessage(ResultCode));
  end;

  StartedAt := GetTickCount;
  while CheckForMutexes('DeskSentry.SingleInstance') and
    (GetTickCount - StartedAt < 10000) do
    Sleep(100);
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
