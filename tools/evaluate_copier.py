"""Executable backend evaluation, not a production dependency.

Run: uv run --no-project --with copier==9.9.1 python tools/evaluate_copier.py
Direct Copier copy cannot enforce our ownership/CAS transaction contract. Even
with tasks disabled, it replaces edited output with overwrite=True; wrapping it
would still require the complete ownership/journal layer plus a Jinja sandbox.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

import copier
from copier.errors import UnsafeTemplateError


def main():
    assert copier.__version__ == "9.9.1", "evaluation is version-pinned"
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        template = root / "template"
        template.mkdir()
        (template / "copier.yml").write_text("name:\n  default: demo\n", encoding="utf-8")
        (template / "generated.txt.jinja").write_text("{{ name }}\n", encoding="utf-8")
        first, second = root / "first", root / "second"
        for destination in (first, second):
            copier.run_copy(str(template), destination, defaults=True, quiet=True)
        assert (first / "generated.txt").read_bytes() == (second / "generated.txt").read_bytes()
        (first / "generated.txt").write_text("handwritten edit\n", encoding="utf-8")
        copier.run_copy(str(template), first, defaults=True, overwrite=True, quiet=True)
        assert (first / "generated.txt").read_text() != "handwritten edit\n"
        (template / "copier.yml").write_text("_tasks:\n  - echo untrusted\n", encoding="utf-8")
        try:
            copier.run_copy(str(template), root / "unsafe", defaults=True, quiet=True)
        except UnsafeTemplateError:
            pass
        else:
            raise AssertionError("untrusted tasks must be rejected")
    print(
        "Copier 9.9.1: deterministic copy PASS; untrusted task refusal PASS; "
        "modified-file preservation FAIL (expected counterexample). Use narrow renderer."
    )


if __name__ == "__main__":
    main()
