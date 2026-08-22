[Setup]
AppName=R9Tools
AppVersion=1.2.4
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
; The Interception kernel driver install step used to live here as a plain
; [Run] entry (Filename: install-interception.exe /install, runhidden
; waituntilterminated). It has been moved into [Code] -- see
; CurStepChanged(ssPostInstall) below -- because a plain [Run] entry gives
; Setup no way to inspect the child process's exit code or output, which
; made a real, confirmed failure mode completely invisible:
;
; CONFIRMED root cause (2026-08-22) of "completely non-functional after a
; fresh install": install-interception.exe succeeds but always prints
; "Interception successfully installed. You must reboot for it to take
; effect." -- Interception is a legacy class upper-filter driver, loaded by
; the PnP manager only when the Keyboard/Mouse device stacks next
; enumerate, i.e. only after a reboot. Nothing in this installer previously
; surfaced that requirement: the [Run] entry ran silently (runhidden,
; waituntilterminated, no result inspected), and the "Launch R9Tools"
; postinstall entry below then launched the app in the SAME, not-yet-
; rebooted session, so RecoilEngine._listenLoop's device-handle-open retry
; loop correctly retried 20 times and got 0/20 every time -- there was no
; driver loaded for it to find, and no amount of retrying in-process could
; ever fix that. This was empirically verified by running
; install-interception.exe /install directly (elevated) on a machine
; where the driver had never been loaded: sc query for the driver's real
; service names ("keyboard"/"mouse" -- see the [Code] comment below and
; main.py's _INTERCEPTION_SERVICES, which previously used the wrong,
; never-existent "keyboard_filter"/"mouse_filter" names) still failed
; immediately afterward, exactly matching the reboot-required message.
;
; The [Code] section below now captures install-interception.exe's exit
; code/output via ExecAndCaptureOutput, surfaces an outright failure via
; MsgBox (interactive installs only -- never blocks a silent/unattended
; run), and -- critically -- tells Setup a restart is required via
; NeedRestart() whenever the tool's own output says so. Per Inno Setup's
; own [Run]-section documentation (see the "postinstall" flag's docs):
; "If Setup has to restart the user's computer ... there will not be an
; opportunity for the checkbox to be displayed and therefore the entry
; will never be processed" -- so returning True from NeedRestart() also
; has the desirable side effect of preventing "Launch R9Tools" below from
; auto-launching the app before the required reboot, for a genuinely fresh
; install. This does not affect the silent self-update path in updater.py,
; which already passes /NORESTART -- and, per a second fix below
; (IsInterceptionDriverActive()), no longer even attempts to reinstall the
; driver at all when it's already loaded, since that reinstall attempt was
; found to fail outright (locked .sys file) rather than being a harmless
; no-op as previously assumed.

; Launch R9Tools after install (optional, user can uncheck)
;
; shellexec is required here, not just stylistic: [Run] entries with the
; postinstall flag default to the runasoriginaluser flag's behavior (per
; Inno Setup's [Run] & [UninstallRun] section docs, "runasoriginaluser"
; topic) -- i.e. Setup de-elevates back to the original, normally
; non-elevated, pre-UAC-dialog user credentials before launching. A plain
; (non-shellexec) [Run] entry launches via a CreateProcess-style call
; (see the "runasoriginaluser"/"Exec" topics), which has no concept of a
; target's requireAdministrator manifest and cannot auto-elevate; it just
; fails with ERROR_ELEVATION_REQUIRED (740). shellexec instead launches via
; a ShellExecute-style call (per the "shellexec" flag's doc: "the file will
; be opened ... the same way it would be if the user double-clicked the
; file in Explorer"), which *is* manifest-aware and will trigger a UAC
; elevation prompt as needed, matching normal double-click behavior for an
; admin-required .exe. R9Tools.exe requires administrator privileges
; (PyInstaller uac_admin=True manifest), so this entry needs shellexec for
; the same underlying reason the silent auto-update relaunch below needs
; ShellExec() over Exec(). shellexec has no documented conflict with
; nowait/postinstall/skipifsilent (only 32bit/64bit/logoutput are listed as
; incompatible with it).
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

// Returns True only when the given Interception filter service ("keyboard"
// or "mouse") is registered AND currently RUNNING. A registered-but-STOPPED
// service (or one that doesn't exist as a service at all yet) deliberately
// does NOT count as active here: install-interception.exe writing to a
// driver .sys file that isn't currently loaded/locked is safe, and
// re-running /install in that case is exactly how a driver that got
// registered but never had its post-install reboot ends up loaded. Only a
// RUNNING state means the .sys file is currently open/locked by the running
// driver, which is the case a reinstall attempt cannot safely handle (see
// IsInterceptionDriverActive() and InstallInterceptionDriver() below).
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

// True only when BOTH the keyboard and mouse Interception filter services
// are RUNNING, i.e. the driver is fully loaded and its .sys files are
// locked. install-interception.exe installs both filters as a single
// operation, so an asymmetric state (one RUNNING, one not) should be rare in
// practice; this deliberately requires both to be RUNNING before skipping
// the install step below, erring toward re-running /install (safe when a
// file isn't locked) rather than silently leaving a half-loaded driver in
// place.
function IsInterceptionDriverActive(): Boolean;
begin
  Result := IsInterceptionServiceRunning('keyboard') and
    IsInterceptionServiceRunning('mouse');
end;

// Installs the Interception kernel driver and, unlike the old plain [Run]
// entry it replaces, actually looks at the result instead of discarding it.
//
// CONFIRMED root cause (2026-08-22) of "completely non-functional after a
// fresh install": install-interception.exe succeeds but always prints
// "Interception successfully installed. You must reboot for it to take
// effect." (verified by running it directly, elevated, on a machine where
// the driver had never been loaded — the real service names it creates are
// "keyboard"/"mouse" per HKLM\SYSTEM\CurrentControlSet\Services, NOT
// "keyboard_filter"/"mouse_filter" — see main.py's _INTERCEPTION_SERVICES
// comment for the app-side half of that naming bug). Interception is a
// legacy class upper-filter driver: the PnP manager only loads it into the
// Keyboard/Mouse device stacks on the *next* boot, so launching R9Tools
// immediately after a fresh install (same session, no reboot yet) always
// finds 0 device handles no matter how many times RecoilEngine._listenLoop
// retries — there is nothing there yet to open.
//
// SECOND confirmed finding (2026-08-22, while investigating the above): this
// procedure used to run install-interception.exe /install unconditionally on
// every single install, including every silent auto-update triggered by
// updater.py. Running /install again on a machine where the driver is
// already installed AND running was tested directly and fails outright —
// exit code 1, stderr "Could not write to \system32\drivers" — because
// Windows won't let the installer overwrite the currently-loaded, locked
// .sys file. That failure was harmless only by accident (the locked file
// simply couldn't be touched), and on a silent run (WizardSilent() true,
// true for every auto-update) it was completely invisible: the failure
// MsgBox below is suppressed for silent runs, so only the Log() call
// recorded it. Rather than continuing to rely on that failure being benign,
// IsInterceptionDriverActive() above is now checked first and the /install
// attempt is skipped entirely whenever the driver is already loaded — so
// /install now only actually runs for a genuine first install, or a prior
// install that never got its post-install reboot, or a machine where the
// driver is missing/broken.
//
// ExecAndCaptureOutput (rather than a plain [Run] entry) is what makes the
// case where /install DOES run diagnosable/fixable: it gives us the exit
// code and stdout/stderr text, so we can (a) tell the user outright if the
// install step failed instead of silently continuing as if it succeeded,
// and (b) detect the "you must reboot" message and act on it via
// NeedRestart() below, which also has the side effect (per Inno Setup's
// [Run]-section docs on the "postinstall" flag) of skipping the "Launch
// R9Tools" checkbox entirely when a restart is pending, so the app is never
// launched before the driver is actually loaded. Uses Setup's own (elevated)
// credentials, same as the [Run] entry it replaces did by default (see
// "runascurrentuser" in the [Run] & [UninstallRun] docs — that's the default
// when postinstall isn't used, so this was never an elevation problem;
// install-interception.exe has no manifest of its own either, confirmed via
// a resource scan finding no embedded <assembly>/<requestedExecutionLevel>
// manifest at all).
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
    // Previously totally silent (runhidden + waituntilterminated + a plain
    // [Run] entry with no result inspection at all) — a failure here meant
    // the app would launch anyway with no driver, no error, and no way for
    // anyone to know why hotkeys/recoil/macros didn't work short of manual
    // `sc query` investigation. Only show a MsgBox for interactive installs
    // — never block/prompt during a silent (e.g. auto-update) run; the
    // Log() call above still records the failure either way.
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
    // Only set this for interactive installs. By the time this line can even
    // be reached, IsInterceptionDriverActive() above has already confirmed
    // the driver was NOT already loaded, so this is a genuine first install
    // (or a prior install that never got its reboot) actually requiring one
    // — not the old, incorrect assumption that a silent run here was "just"
    // re-registering an already-loaded driver. The silent self-update path
    // (updater.py) already passes /NORESTART regardless, and WizardSilent()
    // installs don't show the Finished page / postinstall checkboxes this is
    // meant to gate anyway — but per the fix above, updater.py's silent runs
    // should now skip the /install attempt entirely in the normal case where
    // the driver from a prior real install is already active.
    gInterceptionNeedsRestart := True;
  end;
end;

// Return True to make Setup prompt the user to restart at the end of a
// successful *interactive* installation — see InstallInterceptionDriver()
// above for why this specific driver needs it. Documented behavior (Inno
// Setup Pascal Scripting event reference, "NeedRestart"): "Return True to
// instruct Setup to prompt the user to restart the system at the end of a
// successful installation, False otherwise."
function NeedRestart(): Boolean;
begin
  Result := gInterceptionNeedsRestart;
end;

// The [Run] "Launch R9Tools" entry above uses "skipifsilent" so it only
// fires from the optional checkbox on the interactive Finished page (and is
// correctly skipped for unattended/silent installs, per Inno Setup's
// documented skipifsilent behavior). The in-app self-updater, however,
// launches this installer with /VERYSILENT to perform an unattended update
// and needs the app to come back up on its own afterward, without a user
// present to click anything. WizardSilent() in a CurStepChanged handler is
// the standard documented pattern for "run something after a silent install
// completes" — see the Inno Setup help topic "Silent Mode (/SILENT,
// /VERYSILENT)" and the CurStepChanged Pascal Scripting reference topic.
//
// R9Tools.exe requires administrator privileges (PyInstaller uac_admin=True
// manifest). Exec() launches via a plain CreateProcess-style call, which
// cannot satisfy a target's requireAdministrator manifest — Windows returns
// ERROR_ELEVATION_REQUIRED (740) instead of elevating, since only a
// shell-aware launch can honor a manifest's elevation request. ShellExec
// uses ShellExecuteEx internally, which does handle this correctly. Use an
// empty Verb (defaults to "open") rather than "runas": Setup.exe is already
// running elevated (PrivilegesRequired=admin above), and an explicit
// "runas" verb here could otherwise trigger a redundant second UAC prompt.
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
    InstallInterceptionDriver();

  if (CurStep = ssPostInstall) and WizardSilent() then
  begin
    // Real-world testing of the silent auto-update path (v1.1.3 -> v1.1.4)
    // showed the relaunched R9Tools.exe occasionally fail at the PyInstaller
    // onefile bootloader level ("Failed to load Python DLL ...\_MEIxxxxx\
    // python314.dll") immediately after this ShellExec fired -- even though
    // manually double-clicking the freshly installed R9Tools.exe afterward
    // worked fine. That points to a transient race right after the silent
    // install's file-copy step finishes: ssPostInstall fires with no
    // settling time, and this machine's antivirus (Microsoft Defender, real-
    // time protection confirmed enabled) is known to actively scan/quarantine
    // R9Tools's own on-disk files (observed quarantining R9Tools.sys as
    // "VulnerableDriver:WinNT/Winring0" in Defender's own history) -- a
    // freshly-launched process extracting a brand new python314.dll into a
    // brand new _MEI temp folder is exactly the kind of write AV real-time
    // scanning contends with. This Sleep is a pragmatic mitigation for that
    // environmental/timing race, not a root-cause fix -- it gives the
    // filesystem and any AV scan queue a moment to settle before the
    // relaunch attempts to self-extract again.
    Sleep(2000);
    ShellExec('', ExpandConstant('{app}\R9Tools.exe'), '', '', SW_SHOWNORMAL,
      ewNoWait, ResultCode);
  end;
end;
