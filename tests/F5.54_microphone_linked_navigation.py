#!/usr/bin/env python3
"""FB1/FB2/FB3/FB6/FB7/FB8: repeat the complete navigation regression as a linked release."""

from pathlib import Path
import runpy


def main():
    """Reuse the same capture assertions; keep each browser suite independently bounded."""
    tests = runpy.run_path(str(Path(__file__).with_name("F5.53_microphone_navigation.py")))
    tests["main"](origin="linked")


if __name__ == "__main__":
    main()
