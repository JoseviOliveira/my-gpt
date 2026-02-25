#!/usr/bin/env python3
"""CLI wrapper for the modular benchmark runner."""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from benchmark.cli import main


if __name__ == "__main__":
    main()
