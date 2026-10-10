# Consumer rename inventory

Original scripts consumer at `68f12bc5b4baf845916a77e5e47dd0e5b6ab0da6`. Generated with `python tools/rename_inventory.py --consumer PATH`. Apply the mechanisms in [the framework inventory](rename-inventory.md); immutable historical evidence and release hashes must not be blindly replaced.

| Consumer file | Matching lines |
| --- | --- |
| `AGENTS.md` | 5, 6, 15, 30, 49, 52, 83 |
| `CHANGELOG.md` | 3, 11, 31, 55, 293, 559, 641, 671, 694, 731, 752, 793, 813, 820, 829, 830, 840, 847, 850 |
| `CLAUDE.md` | 5, 6, 15, 30, 49, 52, 83 |
| `README.md` | 165, 167, 168, 178, 184, 198, 207, 212, 215 |
| `aikit` | 62, 67, 2568 |
| `docs/aikit-gateway.md` | 380 |
| `docs/aikit.md` | 323 |
| `docs/keyferry.md` | 244 |
| `docs/manager-migration.md` | 3, 42, 53 |
| `docs/medcat.md` | 72, 333 |
| `docs/netsy.md` | 120 |
| `docs/pluck.md` | 157 |
| `docs/runtime-migration.md` | 3, 8, 10, 12, 15, 16, 17, 18, 41 |
| `docs/scriptkit.md` | 1, 8, 11, 29, 42, 43, 56, 71, 78, 86, 97, 109, 121, 131, 136, 172, 205, 269 |
| `docs/voxtract.md` | 143 |
| `keyferry` | 63, 152 |
| `medcat` | 100 |
| `netsy` | 42 |
| `pluck` | 65 |
| `pokanop_manager/__init__.py` | 1 |
| `pokanop_manager/lifecycle.py` | 3, 56, 107, 208, 230 |
| `pokanop_manager/locks.py` | 1, 19, 21, 22, 63 |
| `pyproject.toml` | 13, 14 |
| `requirements/base.txt` | 2 |
| `requirements/runtime-constraints.txt` | 2 |
| `scripts` | 35, 39, 40, 199, 211, 492, 525, 541, 542, 660, 705, 926, 993, 999 |
| `templates/tool_template.py` | 14, 26, 31 |
| `tests/conftest.py` | 1, 18, 30 |
| `tests/rehearse_generated_tool.py` | 10, 11, 12 |
| `tests/rehearse_runtime_migration.py` | 43, 45, 46, 58, 59, 82, 87, 116, 135, 148, 162, 163, 166, 169 |
| `tests/test_app.py` | 1, 3, 15 |
| `tests/test_blocks.py` | 1, 7, 8 |
| `tests/test_cli.py` | 1, 7 |
| `tests/test_cli_progress_tables.py` | 1, 7, 8, 9, 10, 75, 104 |
| `tests/test_config.py` | 1, 6, 7 |
| `tests/test_console.py` | 1, 7, 33, 44, 89 |
| `tests/test_doctor.py` | 1, 5, 9 |
| `tests/test_external_runtime.py` | 16, 18, 19, 20, 23, 28, 31, 32, 54, 98, 108 |
| `tests/test_manager_migration.py` | 109 |
| `tests/test_manager_recovery.py` | 36 |
| `tests/test_manager_review.py` | 63 |
| `tests/test_proc.py` | 1, 8, 9 |
| `tests/test_style.py` | 1, 3 |
| `tests/test_text.py` | 1, 3 |
| `tests/test_tools_characterization.py` | 3 |
| `voxtract` | 108, 111, 112 |
