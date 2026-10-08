"""Run with --json for one machine result; otherwise exercise shared renderers."""

import argparse

from scriptkit.command import run
from scriptkit.doctor import Check
from scriptkit.output import OutputContext, OutputPolicy, TableData, Theme


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--ascii", action="store_true")
    args = parser.parse_args()
    output = OutputContext(
        OutputPolicy(machine=args.json, ascii=args.ascii), theme=Theme(heading="bold magenta")
    )
    data = TableData(("Tool", "State"), (("example", "ready"),))
    if args.json:
        return run(output, data.to_data)

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
