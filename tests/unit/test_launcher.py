"""The `bj ui` launcher.

A launcher is the difference between "clone the repository and run uvicorn with
these arguments" and "run one command", which is the whole point of the local
app. It is also the one subcommand in the engine that needs something outside
the standard library, so the tests here cover what that costs: it must stay
importable without the extra, and it must not quietly expose an unauthenticated
service to the network.

`uvicorn.run` is replaced throughout. Nothing here binds a port -- a test that
starts a server is a test that hangs on a busy machine.
"""

from __future__ import annotations

import sys

import pytest

from blackjack import cli


@pytest.fixture
def served(monkeypatch):
    """Run `bj ui` without serving, and report what it would have served."""
    uvicorn = pytest.importorskip("uvicorn")
    pytest.importorskip("fastapi")  # loading the service needs it, not just serving
    calls: list[dict] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    # The browser thread would outlive the test and probe a port nobody opened.
    monkeypatch.setattr(cli, "_wait_then_open", lambda *a, **k: None)
    return calls


def test_the_default_address_is_loopback(served) -> None:
    """The security property, stated as a test.

    This service has no authentication of any kind, so the default must be
    reachable only from the machine running it. Widening that is a decision
    someone takes explicitly, on the command line, where it is visible.
    """
    assert cli.main(["ui", "--no-browser"]) == 0
    assert served == [{"host": "127.0.0.1", "port": 8765, "log_level": "warning"}]


def test_serving_off_loopback_says_what_that_means(served, capsys) -> None:
    """A warning, because the consequence is not obvious from the flag."""
    assert cli.main(["ui", "--host", "0.0.0.0", "--no-browser"]) == 0
    assert served[0]["host"] == "0.0.0.0"

    out = capsys.readouterr().out
    assert "not loopback" in out
    assert "no authentication" in out
    # And it still tells you where to go, in a form a browser can use:
    # 0.0.0.0 is an address to bind, not one to visit.
    assert "http://127.0.0.1:8765/" in out


def test_the_url_it_prints_is_the_host_you_asked_for(served, capsys) -> None:
    """Anything but a wildcard is printed as given -- a tailnet address works."""
    assert cli.main(["ui", "--host", "100.64.0.1", "--port", "9100", "--no-browser"]) == 0
    assert "http://100.64.0.1:9100/" in capsys.readouterr().out


def test_a_missing_extra_is_an_instruction_not_a_traceback(monkeypatch, capsys) -> None:
    """The engine has no dependencies, so this is the expected first run.

    Hiding `uvicorn` from the import machinery is the honest way to test it:
    patching a flag would test the flag.
    """
    monkeypatch.setitem(sys.modules, "uvicorn", None)
    assert cli.main(["ui"]) == 1
    err = capsys.readouterr().err
    assert "needs the 'api' extra" in err
    assert "uv sync --extra api" in err


def test_the_service_is_found_from_the_repository_root() -> None:
    """`bj` is a console script, so the working directory is not on sys.path.

    Without the lookup this raised ModuleNotFoundError for `apps` even when run
    from inside a checkout, which is the only place it can work at all.
    """
    pytest.importorskip("fastapi")
    app = cli._load_web_service()
    assert app.title == "Blackjack Solver"


def test_waiting_for_a_port_that_never_opens_gives_up_quietly() -> None:
    """Failing to open a browser must not stop the server it was waiting for.

    Port 1 on loopback refuses immediately, so this exercises the retry path
    and the deadline rather than a timeout.
    """
    cli._wait_then_open("http://127.0.0.1:1/", 1, "127.0.0.1", timeout=0.3)


def test_the_launcher_does_not_cost_the_engine_its_independence() -> None:
    """ADR-0004: nothing third-party at module scope, extras inside functions.

    `tests/unit/test_no_dependencies.py` enforces this across the engine; this
    states it for the one module that now reaches outside the standard library,
    because that module is where the rule is easiest to break by accident.
    """
    import ast
    from pathlib import Path

    source = Path(cli.__file__).read_text(encoding="utf-8")
    scope = {
        name.split(".")[0]
        for node in ast.parse(source).body
        if isinstance(node, ast.Import | ast.ImportFrom)
        for name in (
            [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
        )
    }
    # Guard against the three assertions below passing because the walk above
    # found nothing, which is how a test like this rots.
    assert {"argparse", "sys", "blackjack"} <= scope, scope

    assert "uvicorn" not in scope
    assert "apps" not in scope
    assert "fastapi" not in scope
