"""Fail closed when a suite is empty, failed, or mostly skipped."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def verify(path: Path, minimum: int = 150) -> None:
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    passed = sum(
        not any(case.find(tag) is not None for tag in ("skipped", "failure", "error"))
        for case in cases
    )
    if (
        passed < minimum
        or next(root.iter("failure"), None) is not None
        or next(root.iter("error"), None) is not None
    ):
        raise ValueError(f"Suite did not meet gate: {passed} passed, minimum {minimum}")


if __name__ == "__main__":
    verify(Path(sys.argv[1]))
