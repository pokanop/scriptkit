# Opt-in output contracts (v1)

Import `OutputContext`, `OutputPolicy`, `Theme` and `TableData` from
`scriptkit.output`; command boundaries live in `scriptkit.command`.
Existing root exports, signatures, shared legacy Rich consoles, `dispatch`,
`parse_args` and visual defaults are unchanged. Migration is explicit: pass one
context through your command/services (it implements `contracts.ports.Output`).
Do not mix legacy stdout helpers into machine output outside the command boundary.
No runtime imports manager, registry, generator or AI components.

## Policy

A context snapshots environment variables and binds streams at construction.
It owns one shared console per stream and one multiplexed progress display;
there is no process-global console replacement. Construct it after parsing your
application's output flags. Flags are application-owned, not silently injected.

- Results use stdout; all semantic diagnostics use stderr.
- `machine=True` disables color, animation and prompts. `quiet=True` suppresses
  info/success and progress, but preserves warning/error and requested results.
- Color precedence: machine / explicit `never`, explicit `always`, **presence**
  of nonempty `NO_COLOR`, nonempty `FORCE_COLOR`, then stream TTY. Empty
  environment values are ignored; `FORCE_COLOR=0` disables automatic color.
  Nonempty NO_COLOR wins when both exist. Legacy helpers are unchanged (their
  historical FORCE_COLOR=0 behavior differs from this opt-in standards policy).
- Animation requires an actual stderr TTY, Rich, progress enabled and neither
  quiet nor machine mode. Forced color never forces animation on pipes.
  Monochrome terminals still get progress. Plain/no-Rich output has no animation.
- `rich=False` selects plain output even with Rich installed. Basic semantic
  ANSI styles work without Rich; extended Rich theme tokens require Rich.
- Width is explicit (default 80, minimum 10); Rich wraps to that width; plain
  tables truncate cells to a deterministic equal-column budget. Extremely many
  columns may exceed the budget (one character per column plus separators).
- `ascii=True` escapes non-ASCII text, uses ASCII table borders and a text-only
  default progress display. Custom renderables/columns are responsible for their
  own ASCII compliance. Rich text is literal by default, never interpreted as
  markup. Console extensions should similarly use `Text` for untrusted strings.
- `ask` reads only interactive TTY stdin. Otherwise an explicit default is
  returned, or ValueError is raised. EOF uses the same rule. Ctrl-C propagates.

## Machine envelope

`result(data)` writes one compact UTF-8 JSON object plus newline, flushed to
stdout. Data must be JSON serializable; NaN/Infinity are rejected before writing.
ASCII mode escapes Unicode. Structure (including null fields) is stable:

```json
{"schema_version":1,"ok":true,"data":{"answer":42},"error":null}
```

Failure: `ok=false`, `data=null` for command failures and `error` a diagnostic
string. A doctor failure retains structured check data. `TableData.to_data()`
returns `{"columns":[...],"rows":[[...]]}` in input order without inferred keys,
stringification, markup parsing or environment-dependent fields. Doctor returns
section names mapped to ordered `{label,state,detail,hint}` checks.

Consumers must reject unsupported schema versions, tolerate new data fields,
and use the process exit code for success. Incompatible envelope changes require
a new schema version. There is no timestamp or implicit host metadata. Diagnostic
text is not a machine contract; do not parse stderr.

## Command lifecycle

`command.run(context, main)` calls a callback that **returns data**, producing
exactly one envelope in machine mode. Emitting `result`, `table` or `doctor`
inside that callback is rejected before writing, producing one failure envelope.
Return `TableData.to_data()` for tables, or `context.doctor_data(sections)` for
checks. `CommandResult(data, exit_code, error)` (from `scriptkit.output`) carries
structured data with a nonzero status; a plain integer return is result data.
Standalone emitting APIs remain usable outside the boundary.

In machine mode Python `print`/argparse help on stdout is redirected to stderr.
Argparse stderr is also bound to context diagnostics. Help/version exit 0 with
a null-data success envelope; syntax errors exit 2. Exceptions exit 1, Ctrl-C
130. No default command runs unless `allow_default=True`, and even then only
on completely empty argv: flags, help/version and unknown commands cannot
accidentally trigger work. Bare discovery/help remains application-owned.

A broken output pipe terminates successfully (0), closes stdout and avoids an
interpreter-shutdown traceback. Exception messages are exposed on stderr and
in error envelopes: callers must redact secrets before raising. Unexpected
exceptions get no traceback in this opt-in boundary; legacy `run_cli` retains
its debugging behavior. The boundary redirects process-global Python streams:
call it once on the main thread, not concurrently. Native writes/subprocess
inherited descriptors are outside its interception; capture their output
explicitly. Inject contexts into worker code rather than printing globally.

## Extensions and examples

See `examples/output.py`. Use `context.console()` / `console(diagnostic=True)`
for Rich extensions; never instantiate unrelated global consoles. Do not print
human renderables in machine mode. `table(..., customize=...)` allows public
Rich Table customization and ignores presentation callbacks in machine mode.
Theme supplies semantic styles without changing tool branding globally.

`progress(..., columns=...)` accepts public Rich progress columns. Overlapping
threads share one display; the first active caller chooses its columns until
the last task exits. Handles support thread-safe `advance`, including harmless
late updates after closure. Context managers remove tasks on success, failure,
Ctrl-C and generator closure. They do not start/cancel workers: callers own
worker cancellation and joining. Rich owns a daemon refresh thread while its
live display is active; the context stops that display when its final task exits.

## Verification

`tests/test_output.py` covers policy matrices, golden plain/JSON output, Rich
literal rendering/widths, ASCII, shared consoles, concurrent monochrome progress,
cancellation, custom columns, prompts and doctor. `tests/test_command.py` covers
subprocess stdout purity, help/version, errors 1/2, interrupt 130, safe defaults
and closed pipes (real POSIX descriptor plus portable fake-stream test).
The same tests execute from installed bare and Rich wheels in the existing
Linux/macOS/Windows × Python 3.11–3.14 CI matrix. Terminal emulation tests are
not a claim of pixel-identical rendering across terminal emulators.
