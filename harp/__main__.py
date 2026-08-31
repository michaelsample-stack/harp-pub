"""Allow `python -m harp` as well as the installed `harp` console script."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
