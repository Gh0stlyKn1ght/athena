"""Package execution entrypoint supporting `python -m athena`."""

from __future__ import annotations

import sys

from athena.cli import main

if __name__ == "__main__":
    sys.exit(main())
