"""Support python -m scriptkit using the same installed command boundary."""

from .entrypoint import main

raise SystemExit(main())
