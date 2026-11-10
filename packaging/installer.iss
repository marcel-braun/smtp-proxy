#define AppVersion "1.0.0"

[Setup]
AppId={{FA9494A2-BD10-48BC-B3DC-C42D0EDE46E0}
AppName=SMTPProxy
AppVersion={#AppVersion}
AppPublisher=Marcel Braun
DefaultDirName={autopf}\SMTPProxy
UsePreviousAppDir=no
DisableDirPage=yes
DefaultGroupName=SMTPProxy
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=..\dist
OutputBaseFilename=SMTPProxy-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\app_gui.exe
SetupLogging=yes
CloseApplications=yes
CloseApplicationsFilter=app_gui.exe
RestartApplications=no

[Files]
Source: "..\dist\SMTPProxy\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\SMTPProxy"; Filename: "{app}\app_gui.exe"; WorkingDir: "{app}"

[Code]
var
  DeleteApplicationData: Boolean;

function GetFileAttributesW(FileName: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';

function NoReparsePoints(const Directory: String): Boolean;
var
  FindRec: TFindRec;
begin
  Result := False;
  if (GetFileAttributesW(Directory) and $400) <> 0 then exit;
  Result := True;
  if FindFirst(Directory + '\*', FindRec) then begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') then begin
          if (FindRec.Attributes and $400) <> 0 then begin
            Result := False;
            exit;
          end;
          if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
            if not NoReparsePoints(Directory + '\' + FindRec.Name) then begin
              Result := False;
              exit;
            end;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

function ServiceCommand(const Command: String): Boolean;
var
  ExitCode: Integer;
begin
  Result := Exec(ExpandConstant('{app}\app_service.exe'), Command,
                 ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode);
  if Result then Result := ExitCode = 0;
  if not Result then Log('Service command failed: ' + Command + '; code=' + IntToStr(ExitCode));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if CompareText(RemoveBackslashUnlessRoot(ExpandConstant('{app}')),
                 ExpandConstant('{autopf}\SMTPProxy')) <> 0 then begin
    Result := 'SMTPProxy must be installed in the standard Program Files location.';
    exit;
  end;
  if DirExists(ExpandConstant('{app}')) then begin
    if not NoReparsePoints(ExpandConstant('{app}')) then begin
      Result := 'Unsafe reparse point in installation directory.';
      exit;
    end;
    if FileExists(ExpandConstant('{app}\app_service.exe')) then
      if not ServiceCommand('stop') then
        Result := 'Could not stop SMTPProxy. No binaries have been replaced.';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    if not ServiceCommand('install') then
      RaiseException('SMTPProxy service registration failed. Setup cannot complete.');
end;

function InitializeUninstall(): Boolean;
begin
  Result := False;
  DeleteApplicationData := False;
  if not UninstallSilent then begin
    if MsgBox('Remove SMTPProxy?', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) <> IDYES then exit;
    DeleteApplicationData := MsgBox(
      'Also permanently delete configuration, credentials, queued mail and logs? ' +
      'Choose No to retain all application data.', mbConfirmation,
      MB_YESNO or MB_DEFBUTTON2) = IDYES;
  end;
  if not NoReparsePoints(ExpandConstant('{app}')) then begin
    MsgBox('Unsafe installation directory. Uninstall aborted.', mbError, MB_OK);
    exit;
  end;
  if not ServiceCommand('stop') then begin
    MsgBox('Could not stop SMTPProxy. Uninstall aborted.', mbError, MB_OK);
    exit;
  end;
  if not ServiceCommand('remove') then begin
    MsgBox('Could not remove SMTPProxy service. Uninstall aborted.', mbError, MB_OK);
    exit;
  end;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Directory: String;
begin
  if (CurUninstallStep = usPostUninstall) and DeleteApplicationData then begin
    Directory := ExpandConstant('{commonappdata}\SMTPProxy');
    if DirExists(Directory) then
      if (not NoReparsePoints(Directory)) or (not DelTree(Directory, True, True, True)) then
        MsgBox('Application data could not be safely deleted and has been retained.', mbError, MB_OK);
  end;
end;