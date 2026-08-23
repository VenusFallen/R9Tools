[Setup]
AppName=R9Tools
AppVersion=1.3.2
AppPublisher=VenusFallen
AppSupportURL=https://github.com/VenusFallen/R9Tools
DefaultDirName={autopf}\R9Tools
DefaultGroupName=R9Tools
OutputBaseFilename=R9Tools_Setup
OutputDir=installer
PrivilegesRequired=admin
Compression=lzma2
SolidCompression=yes
SetupIconFile=assets\R9Tools.ico
UninstallDisplayIcon={app}\R9Tools.exe
; Require Windows 10 or later
MinVersion=10.0

; Closes a running R9Tools.exe via Windows' Restart Manager before the
; [Files] copy step touches it, instead of relying on updater.py's
; launch_installer_and_quit() winning a bare timing race against this
; process's own shutdown teardown (the previous behavior, confirmed as the
; root cause of a real v1.3.0->v1.3.1 update that silently failed to
; replace the running exe). AppMutex names the mutex main.py's
; _create_app_mutex() creates and holds for the app's whole lifetime --
; must match that name exactly (case-sensitive). In silent installs
; (/VERYSILENT /SUPPRESSMSGBOXES, as updater.py always passes) Setup closes
; the app automatically with no prompt.
CloseApplications=yes
AppMutex=R9Tools_AppMutex
; Setup's own post-close auto-relaunch is disabled in favor of this
; script's RelaunchAppAfterSilentUpdate() below (ssPostInstall), which
; retries with a settle delay and verifies via tasklist that the relaunch
; actually stuck -- Setup's default relaunch does neither and would also
; race/duplicate with it.
RestartApplications=no

[Files]
; Main application
Source: "dist\R9Tools.exe"; DestDir: "{app}"; Flags: ignoreversion

; Interception driver installer — kept in app dir so uninstaller can use it
Source: "installer_assets\install-interception.exe"; DestDir: "{app}"; Flags: ignoreversion

; Documentation
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

; Third-party license — LibreHardwareMonitor DLLs bundled in lib/ (MPL 2.0)
Source: "lib\LICENSE-LibreHardwareMonitor.txt"; DestDir: "{app}"; Flags: ignoreversion

; Third-party license — PresentMon.exe bundled in presentmon/ (MIT)
Source: "presentmon\LICENSE-PresentMon.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\R9Tools"; Filename: "{app}\R9Tools.exe"
Name: "{group}\README"; Filename: "{app}\README.md"
Name: "{commondesktop}\R9Tools"; Filename: "{app}\R9Tools.exe"; Tasks: desktopicon

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Run]
; The Interception driver install step lives in [Code] (see
; CurStepChanged(ssPostInstall) below) rather than as a plain [Run] entry,
; because Setup needs to inspect the installer's exit code/output to detect
; the "must reboot" case and skip reinstalling an already-loaded driver.

; Launch R9Tools after install (optional, user can uncheck)
;
; shellexec is required: postinstall [Run] entries launch de-elevated via a
; plain CreateProcess-style call, which can't satisfy R9Tools.exe's
; requireAdministrator manifest and would fail with ERROR_ELEVATION_REQUIRED.
; shellexec uses ShellExecute instead, which triggers the UAC prompt normally.
Filename: "{app}\R9Tools.exe"; Description: "Launch R9Tools"; \
    Flags: nowait postinstall skipifsilent shellexec

[UninstallRun]
; Remove the Interception driver on uninstall
Filename: "{app}\install-interception.exe"; Parameters: "/uninstall"; \
    Flags: runhidden waituntilterminated

[Code]
// Set by InstallInterceptionDriver() below, consumed by NeedRestart().
var
  gInterceptionNeedsRestart: Boolean;

// True only when the given Interception filter service ("keyboard" or
// "mouse") is registered AND currently RUNNING. A registered-but-stopped
// service deliberately doesn't count: re-running /install against it is
// safe and is how a driver missing its post-install reboot gets loaded.
function IsInterceptionServiceRunning(const ServiceName: String): Boolean;
var
  ResultCode: Integer;
  Output: TExecOutput;
  I: Integer;
  CombinedOutput: String;
begin
  Result := False;
  if not ExecAndCaptureOutput('sc.exe', 'query ' + ServiceName, '',
    SW_HIDE, ewWaitUntilTerminated, ResultCode, Output) then
    Exit;

  CombinedOutput := '';
  for I := 0 to GetArrayLength(Output.StdOut) - 1 do
    CombinedOutput := CombinedOutput + Output.StdOut[I] + #13#10;

  Result := Pos('RUNNING', Uppercase(CombinedOutput)) > 0;
end;

// True only when both the keyboard and mouse Interception filter services
// are RUNNING (driver fully loaded, .sys files locked). Requiring both
// errs toward re-running /install rather than leaving a half-loaded driver.
function IsInterceptionDriverActive(): Boolean;
begin
  Result := IsInterceptionServiceRunning('keyboard') and
    IsInterceptionServiceRunning('mouse');
end;

// Runs install-interception.exe, but only if the driver isn't already
// loaded (reinstalling a loaded driver fails outright, locked .sys file).
// The driver only attaches to the device stack on next boot, so a fresh
// install always needs NeedRestart() below.
procedure InstallInterceptionDriver();
var
  ResultCode: Integer;
  Output: TExecOutput;
  I: Integer;
  CombinedOutput: String;
  Succeeded: Boolean;
begin
  if IsInterceptionDriverActive() then
  begin
    Log('InstallInterceptionDriver: keyboard and mouse services are both ' +
      'already RUNNING; skipping install-interception.exe /install -- ' +
      're-running it against an already-loaded driver fails outright ' +
      '("Could not write to \system32\drivers", the .sys file is locked ' +
      'while the driver is running), confirmed via direct testing on ' +
      '2026-08-22.');
    Exit;
  end;

  if not WizardSilent() then
    WizardForm.StatusLabel.Caption := 'Installing Interception driver...';
  Succeeded := ExecAndCaptureOutput(
    ExpandConstant('{app}\install-interception.exe'), '/install', '',
    SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode, Output);

  CombinedOutput := '';
  for I := 0 to GetArrayLength(Output.StdOut) - 1 do
    CombinedOutput := CombinedOutput + Output.StdOut[I] + #13#10;
  for I := 0 to GetArrayLength(Output.StdErr) - 1 do
    CombinedOutput := CombinedOutput + Output.StdErr[I] + #13#10;

  Log('install-interception.exe /install: launched=' + IntToStr(Ord(Succeeded)) +
    ' resultcode=' + IntToStr(ResultCode) + ' output=' + CombinedOutput);

  if (not Succeeded) or (ResultCode <> 0) then
  begin
    // Only show a MsgBox for interactive installs; never block/prompt during
    // a silent (e.g. auto-update) run. The Log() call above still records
    // the failure either way.
    if not WizardSilent() then
      MsgBox('The Interception input driver failed to install ' +
        '(this is required for hotkeys, recoil control, and macros to ' +
        'work).' + #13#10#13#10 + 'Details: ' + CombinedOutput + #13#10 +
        'You can try re-running this installer, or run ' +
        '"install-interception.exe /install" manually as Administrator ' +
        'from the install folder.', mbCriticalError, MB_OK);
  end
  else if (Pos('reboot', Lowercase(CombinedOutput)) > 0) and (not WizardSilent()) then
  begin
    // Only set this for interactive installs; the silent self-update path
    // in updater.py already passes /NORESTART and doesn't show the Finished
    // page this flag gates.
    gInterceptionNeedsRestart := True;
  end;
end;

// Inno Setup event: return True to prompt the user to restart at the end of
// a successful installation. See InstallInterceptionDriver() above for why.
function NeedRestart(): Boolean;
begin
  Result := gInterceptionNeedsRestart;
end;

// True if R9Tools.exe is currently running (via tasklist). Used to verify a
// ShellExec launch actually stuck, since ShellExec with ewNoWait only
// confirms the OS accepted the launch, not that the process stayed alive.
function IsAppProcessRunning(): Boolean;
var
  ResultCode: Integer;
  Output: TExecOutput;
  I: Integer;
  CombinedOutput: String;
begin
  Result := False;
  if not ExecAndCaptureOutput('tasklist.exe',
    '/FI "IMAGENAME eq R9Tools.exe" /NH', '',
    SW_HIDE, ewWaitUntilTerminated, ResultCode, Output) then
    Exit;

  CombinedOutput := '';
  for I := 0 to GetArrayLength(Output.StdOut) - 1 do
    CombinedOutput := CombinedOutput + Output.StdOut[I] + #13#10;

  Result := Pos('R9TOOLS.EXE', Uppercase(CombinedOutput)) > 0;
end;

// The "Launch R9Tools" [Run] entry uses skipifsilent, so a silent
// self-update (updater.py runs this installer with /VERYSILENT) needs to
// relaunch the app itself here instead. Uses ShellExec (not Exec) for the
// same manifest/elevation reason as the [Run] entry above.
//
// The relaunch is retried with an increasing settle delay (2s/4s/6s) and
// verified via IsAppProcessRunning(), because the freshly extracted
// PyInstaller bootloader can occasionally fail to come up right after a
// silent install's file-copy step (observed race with AV real-time
// scanning of the newly written files) even though a later manual launch
// works fine.
procedure RelaunchAppAfterSilentUpdate();
var
  ResultCode: Integer;
  Attempt: Integer;
  SettleDelayMs: Integer;
begin
  SettleDelayMs := 2000;
  for Attempt := 1 to 3 do
  begin
    Sleep(SettleDelayMs);
    ShellExec('', ExpandConstant('{app}\R9Tools.exe'), '', '', SW_SHOWNORMAL,
      ewNoWait, ResultCode);

    // Give the bootloader a moment to either come up or crash outright (the
    // observed "Failed to load Python DLL" failure mode dies well under a
    // second after ShellExec fires) before checking whether it's actually
    // still alive.
    Sleep(1500);

    if IsAppProcessRunning() then
    begin
      Log('RelaunchAppAfterSilentUpdate: R9Tools.exe confirmed running ' +
        'after attempt ' + IntToStr(Attempt) + ' (settle delay was ' +
        IntToStr(SettleDelayMs) + 'ms).');
      Exit;
    end;

    Log('RelaunchAppAfterSilentUpdate: R9Tools.exe not found running after ' +
      'attempt ' + IntToStr(Attempt) + ' (settle delay was ' +
      IntToStr(SettleDelayMs) + 'ms) -- likely the bootloader race described ' +
      'above; retrying with a longer settle delay.');
    SettleDelayMs := SettleDelayMs + 2000;
  end;

  Log('RelaunchAppAfterSilentUpdate: gave up after 3 attempts -- the ' +
    'silent update completed but R9Tools.exe did not stay running. The ' +
    'user will need to launch it manually.');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    InstallInterceptionDriver();

  if (CurStep = ssPostInstall) and WizardSilent() then
    RelaunchAppAfterSilentUpdate();
end;
