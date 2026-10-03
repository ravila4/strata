import subprocess

import pytest

from strata import cli


@pytest.mark.parametrize(
    "stderr", ["agent already loaded\n", b"agent already loaded\n"]
)
def test_failed_command_reports_its_stderr(monkeypatch, capsys, stderr):
    def fail(args):
        raise subprocess.CalledProcessError(5, ["launchctl", "bootstrap"], "", stderr)

    monkeypatch.setattr(cli, "service_remove", fail)
    with pytest.raises(SystemExit) as exit:
        cli.main(["service", "remove"])
    assert exit.value.code == 2
    assert "agent already loaded" in capsys.readouterr().err


def test_failed_command_without_stderr_names_the_command(monkeypatch, capsys):
    def fail(args):
        raise subprocess.CalledProcessError(5, ["launchctl", "bootstrap"])

    monkeypatch.setattr(cli, "service_remove", fail)
    with pytest.raises(SystemExit):
        cli.main(["service", "remove"])
    assert "launchctl bootstrap failed" in capsys.readouterr().err
