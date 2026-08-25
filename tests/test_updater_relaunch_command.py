"""
Unit tests for updater.py's PowerShell installer-relaunch command builder.

launch_installer_and_quit() itself spawns a real detached subprocess, which
isn't meaningfully unit-testable. What IS testable without spawning anything
is the actual command text it builds: _build_relaunch_command() (and its
_quote_ps_single() quoting helper) are pure string functions, split out
specifically so the generated Wait-Process/Start-Process invocation can be
checked here -- this is the part most likely to have a subtle quoting bug
(paths with spaces or embedded quotes, the /LOG=<path> token surviving as
one argument, etc.).
"""
from pathlib import Path

import updater


def test_quote_ps_single_wraps_plain_value():
    assert updater._quote_ps_single("/VERYSILENT") == "'/VERYSILENT'"


def test_quote_ps_single_escapes_embedded_single_quote():
    # PowerShell's single-quoted-string escape is doubling the quote.
    assert updater._quote_ps_single("O'Brien") == "'O''Brien'"


def test_build_relaunch_command_waits_on_the_given_pid():
    cmd = updater._build_relaunch_command(
        4242, Path(r"C:\Temp\R9Tools_Setup.exe"), ["/VERYSILENT"]
    )
    assert "Wait-Process -Id 4242" in cmd
    assert "-Timeout 30" in cmd
    assert "-ErrorAction SilentlyContinue" in cmd
    # The wait must come before the installer is started.
    assert cmd.index("Wait-Process") < cmd.index("Start-Process")


def test_build_relaunch_command_quotes_installer_path_with_spaces():
    installer_path = Path(r"C:\Users\Some User\AppData\Local\Temp\R9Tools_Setup.exe")
    cmd = updater._build_relaunch_command(1, installer_path, ["/VERYSILENT"])
    assert f"-FilePath '{installer_path}'" in cmd


def test_build_relaunch_command_passes_each_install_arg_as_its_own_array_element():
    install_args = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=C:\\log.txt"]
    cmd = updater._build_relaunch_command(1, Path("C:\\Setup.exe"), install_args)

    expected_array = "@(" + ", ".join(f"'{a}'" for a in install_args) + ")"
    assert f"-ArgumentList {expected_array}" in cmd
    # /LOG=<path> must survive as a single token, not be split on the '='.
    assert "'/LOG=C:\\log.txt'" in cmd


def test_build_relaunch_command_hides_the_installer_window():
    cmd = updater._build_relaunch_command(1, Path("C:\\Setup.exe"), ["/VERYSILENT"])
    assert "-WindowStyle Hidden" in cmd
