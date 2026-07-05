#!/usr/bin/env python3
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from recap_agent.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
