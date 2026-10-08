# Provenance

The twelve runtime Python files in `src/scriptkit/` (excluding the new
`entrypoint.py` and `__main__.py`) are imported byte-for-byte from:

- https://github.com/pokanop/scripts
- commit `458104a52bafe619a259ed2b83ecd7a3b29c82c1`
- source paths `scriptkit/*.py`, runtime version 1.3.0
- MIT, Copyright (c) 2026 Pokanop Apps LLC; full original LICENSE retained.

Ten library test modules (`app`, `blocks`, `cli`, `cli_progress_tables`, `config`,
`console`, `doctor`, `proc`, `style`, `text`) and `docs/runtime.md` originate
from that same commit. The Rich-only rendering test now explicitly skips
when Rich is absent. `tests/conftest.py` retains color isolation but removes
consumer-tool loading and source-path injection. Consumer-specific tool,
installer and template tests stay in pokanop/scripts. `docs/runtime.md` is
adapted: its wiring, new-tool and test instructions now describe the installed
standalone wheel rather than the consumer repository's bootstrap and templates.

`tests/public_api_1_3_0.json` was generated from the original runtime, recording
all public exports and inspectable callable signatures (CliError inherits
Exception's constructor; console instances are dependency-dependent).
