"""Allow ``python -m bansos`` when the src directory is on PYTHONPATH."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
