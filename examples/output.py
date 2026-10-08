"""Run with --json for one machine result; otherwise exercise shared renderers."""

import argparse
import sys

from scriptkit.command import run
from scriptkit.doctor import Check
from scriptkit.output import OutputContext, OutputPolicy, TableData, Theme


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--ascii", action="store_true")
    # Resolve only output mode before the boundary, without parsing/printing help.
    # Ignore tokens after --: those are operands, not output flags.
    raw = sys.argv[1:]
    flags = raw[: raw.index("--")] if "--" in raw else raw
    machine = "--json" in flags
    output = OutputContext(
        OutputPolicy(machine=machine, ascii="--ascii" in flags),
        theme=Theme(heading="bold magenta"),
    )
    data = TableData(("Tool", "State"), (("example", "ready"),))
    if machine:

        def machine_main():
            parser.parse_args(raw)
            return data.to_data()

        return run(output, machine_main)
    parser.parse_args(raw)

    def customize(table):
        table.caption = "Uses this context's shared console"

    output.table(data, customize=customize)
    # Optional Rich columns, but never an incompatible new global console.
    if output.console(diagnostic=True) is not None:
        from rich.progress import TextColumn

        columns = (TextColumn("{task.description}: {task.completed}/{task.total}"),)
    else:
        columns = ()
    with output.progress("Checking", total=2, columns=columns) as task:
        task.advance()
        task.advance()
    return output.doctor({"Runtime": [Check.ok("Output", "ready")]})


if __name__ == "__main__":
    raise SystemExit(main())
