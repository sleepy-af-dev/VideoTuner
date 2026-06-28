#!/usr/bin/env python3
"""Top-level entry point for videotuner.

This wrapper ensures the `src/` directory is on sys.path, then delegates to
the package CLI implemented in `videotuner.pipeline`.
"""

import sys
from pathlib import Path


def _bootstrap_src() -> None:
    root = Path(__file__).resolve().parent
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def main() -> int:
    _bootstrap_src()
    from videotuner.pipeline import main as pipeline_main

    return pipeline_main()


if __name__ == "__main__":
    raise SystemExit(main())
