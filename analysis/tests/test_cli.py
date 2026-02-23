from openpulse_analysis.cli import main


def test_cli_help_exits_cleanly() -> None:
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
