import unittest
from unittest.mock import patch, sentinel

from rtc_room_framework import cli


class CLITests(unittest.TestCase):
    def test_main_uses_app_instance_when_reload_is_disabled(self) -> None:
        with patch("rtc_room_framework.cli.create_app", return_value=sentinel.app) as mock_create_app:
            with patch("rtc_room_framework.cli.uvicorn.run") as mock_run:
                cli.main(["--host", "127.0.0.1", "--port", "9001"])

        mock_create_app.assert_called_once_with()
        mock_run.assert_called_once_with(
            sentinel.app,
            host="127.0.0.1",
            port=9001,
            reload=False,
            ssl_certfile=None,
            ssl_keyfile=None,
        )

    def test_main_uses_import_string_factory_when_reload_is_enabled(self) -> None:
        with patch("rtc_room_framework.cli.create_app") as mock_create_app:
            with patch("rtc_room_framework.cli.uvicorn.run") as mock_run:
                cli.main(["--reload"])

        mock_create_app.assert_not_called()
        mock_run.assert_called_once_with(
            "rtc_room_framework.app:create_app",
            host="0.0.0.0",
            port=8000,
            reload=True,
            ssl_certfile=None,
            ssl_keyfile=None,
            factory=True,
        )


if __name__ == "__main__":
    unittest.main()
