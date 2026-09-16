from adaos.apps.cli.app import app


def test_cli_tracebacks_do_not_export_locals_with_runtime_configuration():
    assert app.pretty_exceptions_show_locals is False
