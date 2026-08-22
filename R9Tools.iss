[Setup]
AppName=R9Tools
AppVersion=1.2.0
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
; Install the Interception kernel driver (requires admin, already elevated)
Filename: "{app}\install-interception.exe"; Parameters: "/install"; \
    Flags: runhidden waituntilterminated; \
    StatusMsg: "Installing Interception driver..."

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
