"""Put the repo root on sys.path so tests can import build.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
