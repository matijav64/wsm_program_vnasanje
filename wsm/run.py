"""Entry point for launching WSM in GUI mode or as CLI."""
from __future__ import annotations

import logging
import sys

from wsm.cli import main as cli_main
from wsm.ui.main_menu import launch_main_menu

logging.basicConfig(level=logging.INFO)


def main() -> None:
    if len(sys.argv) > 1:
        cli_main()
    else:
        launch_main_menu()


if __name__ == "__main__":
    main()

