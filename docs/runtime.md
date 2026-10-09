# 🧰 scriptkit — Shared CLI scaffolding

> Adapted from `pokanop/scripts@458104a` for the standalone installed runtime.
> See [provenance](../NOTICE.md). Consumer tools, installers and templates are
> not included in this repository.

**A common library for command-line tools: color, icons, semantic messages,
prompts, progress, tables, three-tier config, subprocess handling,
human-friendly formatting, and CLI dispatch.**

`scriptkit` · Python 3.11+ · `rich` (optional, graceful fallback) · zero other deps

```python
import scriptkit as sk

sk.success("done")                       # ✅ green, stdout
sk.error("nope")                         # ❌ red, stderr
for item in sk.track(items, "Working"):  # spinner + bar + M/N + elapsed
    ...
cfg = sk.Config(path, defaults={...}, env_prefix="MYTOOL").load()
res = sk.run(["git", "status"])          # Result(code, out, err)
sk.doctor("mytool", __version__, sections={...})   # one diagnostic look for all
args = sk.parse_args(parser, default="scan")        # bare run → default command
sys.exit(sk.run_cli(main))               # CliError → exit 1, Ctrl-C → exit 130
```

---

## Why

Every tool had grown its own copy of the same helpers — ANSI codes, `print_success`,
a `run()` wrapper, dot-path config, a progress bar. `scriptkit` is the single,
tested home for those patterns. Change the house style in one place; every tool
updates. New tools start beautiful for free.

It is **import-safe without `rich`**: messages and tables degrade to plain ANSI /
text, so it is even safe for the bootstrap installer running under a bare system
Python.

---

## How it's wired in

Install the built wheel into the same Python environment that runs your tool.
From this repository, with a Python 3.11+ virtual environment activated:

```sh
python -m pip install '.[dev]'
python -m build
python -m pip install dist/pokanop_scriptkit-1.4.0-py3-none-any.whl
# Optional: install the wheel's declared Rich extra instead.
python -m pip install 'dist/pokanop_scriptkit-1.4.0-py3-none-any.whl[rich]'
```

Tools then use `import scriptkit as sk` from any working directory. Do not
vendor the package, search parent directories or modify `sys.path`. Distribution
metadata uses the provisional name `pokanop-scriptkit`; the import is `scriptkit`.
No public package release or namespace availability is implied.

`scriptkit --help` and `python -m scriptkit --version` expose the foundation's
help/version CLI only, not an installer or generator. See the
[compatibility policy](compatibility.md) for API and exit guarantees.

---

## API at a glance

### Messages (`scriptkit.console`)
| Call | Output |
|------|--------|
| `sk.success(text)` | `  ✅ text` — bold green, stdout |
| `sk.error(text)` | `  ❌ text` — bold red, **stderr** |
| `sk.warning(text)` | `  ⚠️  text` — bold yellow |
| `sk.info(text)` | `  ℹ️  text` — dim cyan |
| `sk.detail(text)` | dim continuation line, no icon |
| `sk.step(n, total, text)` | `  [2/5] text` |
| `sk.header(text)` | a section rule (rich) or `━━━ text ━━━` |
| `sk.elapsed(label, secs)` | `  ⏱️  label: 4.2s` |
| `sk.kv(label, value)` | aligned `label: value` |
| `sk.ask(prompt, default)` | line input, `default` on empty/EOF |
| `sk.confirm(prompt, default)` | yes/no, returns bool |

### Color & icons (`scriptkit.style`)
- `sk.styled(text, *codes)` — wrap in ANSI, or strip codes when color is off.
- `sk.use_color(stream=None)` / `sk.set_color(True|False|None)` — honors
  `NO_COLOR` and `FORCE_COLOR`; auto-detects TTY otherwise.
- `sk.icon(name)` — semantic emoji lookup (`success`, `warn`, `clock`, `rocket`, …).
- Color constants: `sk.style.RED`, `BOLD`, `DIM`, `CYAN`, …

### Progress (`scriptkit.progress`)
- `sk.track(iterable, "desc")` — iterate with a spinner+bar+M/N+elapsed (rich) or plainly.
- `sk.track_bytes(chunks, "Downloading", total=bytes)` — stream byte chunks with a
  spinner, bar, transferred size, speed, and ETA; omitted `total` pulses indeterminately.
- `sk.status("message")` — context manager spinner for indeterminate work.
- `sk.parallel_map(fn, items, "desc", max_workers=8)` — threaded map with combined progress.
- `sk.bar(pct, width=30)` — a pure-string `[████░░░] 50%` for inline `\r` updates.

### Tables (`scriptkit.tables`)
```python
sk.table(
    [{"name": "#", "justify": "right"}, "Host", {"name": "Status"}],
    [[1, "router", "[green]up[/]"]],
    title="Hosts",
)
```
Columns are strings or dicts (`name`, `justify`, `style`, `width`, `max_width`,
`no_wrap`). Falls back to an aligned text grid without `rich`.

### Config (`scriptkit.config`)
```python
cfg = sk.Config(path, defaults={"web": {"port": 8765}},
                env_prefix="MYTOOL", coerce_env=True)
data = cfg.load()                 # defaults < file < MYTOOL_WEB__PORT
sk.get_nested(data, "web.port")   # 8765
sk.set_nested(data, "web.host", "0.0.0.0")
cfg.save(data)                    # pretty JSON, chmod 0600
```
`coerce_env`/`coerce=True` turns scalar strings (`"true"`, `"9000"`, `"3.5"`)
into their natural types — handy for env-var overrides.

### Managed blocks (`scriptkit.blocks`)
```python
block = sk.ManagedBlock("# >>> mytool >>>", "# <<< mytool <<<")
block.apply(rc_path, 'export FOO="bar"')   # insert/replace; .bak'd once before first write
block.clear(rc_path)                        # remove it, leaving the rest of the file intact
```
A reversible, idempotent managed-text-region primitive: write the same body twice
and it's a no-op; `clear` is the exact inverse of `apply`. Pure helpers
(`upsert_block` / `remove_block` / `find_block` / `has_block` / `render_block`) do
the splicing if you'd rather operate on strings. Built for shell rc env blocks and
any managed config stanza a tool must add and later take back out cleanly.

### Subprocess (`scriptkit.proc`)
```python
res = sk.run(["git", "rev-parse", "HEAD"])   # Result(code, out, err); res.ok / bool(res)
res = sk.run(cmd, check=True)                 # non-zero → CliError
sk.which("ffmpeg")                            # bool
sk.require("ffmpeg", hint="brew install ffmpeg")  # missing → CliError
```
Timeouts return code `-1`; a missing binary returns `-127`. Never raises for a
non-zero exit unless `check=True`.

### Text (`scriptkit.text`)
`sk.human_size(1536) → "1.5 KB"` · `sk.human_duration(185) → "3m 5s"` ·
`sk.format_timecode(75.5) → "1:15.50"` · `sk.human_count(2, "host") → "2 hosts"` ·
`sk.truncate("hello world", 8) → "hello w…"`

### CLI lifecycle (`scriptkit.cli`)

One lifecycle for every tool — parse → (optional default command) → dispatch →
clean exit. A tool's `main` collapses to four lines:

```python
DEFAULT_COMMAND = None          # or e.g. "scan" / "list" for a default action

def main() -> int:
    parser = build_parser()
    args = sk.parse_args(parser, default=DEFAULT_COMMAND)
    return sk.dispatch(args, HANDLERS, parser, default=DEFAULT_COMMAND,
                       banner=sk.banner("mytool", __version__, TAGLINE, ICON))

if __name__ == "__main__":
    sys.exit(sk.run_cli(main))
```

- **`sk.CliError(msg)`** — raise for expected failures; printed cleanly as
  `❌ msg`, exit 1. **Give each tool its own type by subclassing it**
  (`class PluckError(sk.CliError): ...`); `run_cli` catches the whole family, so
  no tool needs a per-command `try/except`.
- **`sk.run_cli(main, *, on_interrupt=None)`** — wraps `main`: `CliError` → exit
  1, `KeyboardInterrupt` → run `on_interrupt()` (optional cleanup, e.g. removing
  temp files) then exit 130, int return → exit code. **This is the only place
  Ctrl-C is handled** — never hand-roll a `KeyboardInterrupt` handler in a tool.
- **`sk.parse_args(parser, *, default=None)`** — `parser.parse_args`, but when
  `default` is set and the user gave no subcommand (and didn't ask for
  `-h`/`-v`), the default subcommand is injected so its parser defaults populate
  (bare `netsy` → `netsy scan`). Omit `default` and a bare invocation shows
  banner-led help.
- **`sk.dispatch(args, handlers, parser=None, *, default=None, banner=None)`** —
  routes `args.command` to `handlers[cmd](args)`. For any real command it prints
  `banner` **to stderr** (always visible, never pollutes piped stdout); a bare
  invocation with no `default` prints banner-led help and exits 0.

### Doctor (`scriptkit.doctor`)

Every tool's `doctor` uses **one renderer** so they look identical: an
auto-generated **System** section, your **check sections**, a rolled-up
**Issues** list, optional **Tips**, and a verdict with a meaningful exit code
(`1` iff any *required* check failed).

```python
def cmd_doctor(args) -> int:
    return sk.doctor("mytool", __version__, TAGLINE, ICON,
        sections={
            "Prerequisites": [
                sk.check_binary("ffmpeg", hint="brew install ffmpeg"),
                sk.check_binary("optional-bin", required=False, hint="…"),
            ],
            "Python packages": [sk.check_python("rich", required=False)],
            "Config": [sk.Check.ok("Config file", str(CONFIG.path))],
        },
        tips=["a dim line of guidance"])
```

- **`sk.check_binary(name, *, hint="", required=True, version=True)`** — on
  `PATH`? Captures the first line of `--version` as detail. Missing → `FAIL`
  (required) or `WARN` (optional).
- **`sk.check_python(module, *, hint="", required=True)`** — importable? (uses
  `find_spec`, so it's cheap and won't trigger heavy imports).
- **`sk.Check.ok(label, detail="")` / `.warn(label, detail, hint)` /
  `.fail(label, detail, hint)`** — for anything custom (a detected IP, a config
  path). `hint` surfaces in the **Issues** section when not OK.

`sk.doctor` omits the banner because `dispatch` already prints it (to stderr);
pass `show_banner=True` only when calling `doctor` outside the dispatch flow.

### Identity & CLI framing (`scriptkit.app`)

Every tool presents the **same first impression** — one identity line, a
`-v/--version` flag, and an aligned `Examples:` epilog — by building its parser
through `sk.make_parser` instead of `argparse.ArgumentParser`.

```python
ICON = "🚀"                        # one distinct brand emoji per tool
TAGLINE = "does the thing"          # short; shown after the em-dash

parser = sk.make_parser(
    "mytool", __version__, TAGLINE, icon=ICON,
    examples=[("mytool go", "run it"), ("mytool doctor", "check env")],
)
sub = parser.add_subparsers(dest="command")
```

- **`sk.banner(name, version, tagline, icon)`** → the identity line
  `🚀 mytool v1.2.3 — does the thing` (name bold-cyan, version dim, NO_COLOR-aware).
  Use it for `--help` (automatic via `make_parser`) *and* at runtime: `print(banner())`.
- **`sk.make_parser(prog, version, tagline, *, icon, examples=None, epilog=None, …)`**
  — sets the banner description, a `RawDescription` formatter, and a `-v/--version`
  flag. Pass `examples=[(cmd, desc), …]` for an aligned epilog, or your own `epilog=`.
- **`sk.examples_block(items)`** → the aligned `Examples:` block, if you want it standalone.

> **`-v` means `--version` across the whole toolkit.** Don't reuse `-v` for
> `--verbose` (use `--verbose`); a conflicting `-v` will fail to register.

Define `ICON`/`TAGLINE` as module constants so the help banner, `--version`, and any
runtime banner all read from one source of truth.

The current brand emojis: 🛠️ scripts · 🤖 aikit · 🛳️ keyferry · 📚 medcat ·
📡 netsy · 🪶 pluck · 🌊 voxtract. Use `sk.header("Section")` for in-tool section
rules (`━━━ Section ━━━━━`) so sections look identical everywhere.

---

## Building a new tool

After installing the wheel, save this minimal example as `mytool.py` outside
the framework checkout:

```python
import sys
import scriptkit as sk


def main() -> int:
    parser = sk.make_parser("mytool", "0.1.0", "Example installed-runtime tool")
    sk.parse_args(parser)
    sk.success("Ready")
    return 0


if __name__ == "__main__":
    sys.exit(sk.run_cli(main))
```

Run `python mytool.py --help`, `python mytool.py --version` or `python mytool.py`
using the environment where you installed the wheel. Add your handlers using
the lifecycle APIs above; keep your application logic in your own project.

This foundation does not ship scaffold templates, an installer or a tool
registry. Those are separate roadmap components, not files to copy from this
checkout. See [component ownership and template resource strategy](architecture.md)
and [contributor guidance](../CONTRIBUTING.md). Consumer-specific registration
and migration belong to `pokanop/scripts`, not this runtime API.

---

## Tests

From this repository, with a virtual environment activated:

```sh
python -m pip install -e '.[dev,rich]'
python -m pytest
python -m mypy
python -m build
python tools/check_artifact.py dist/pokanop_scriptkit-1.4.0-py3-none-any.whl
python tools/check_artifact.py dist/pokanop_scriptkit-1.4.0-py3-none-any.whl --rich
```

- `tests/test_{app,blocks,cli,cli_progress_tables,config,console,doctor,proc,style,text}.py`
  — inherited library tests, including local subprocess execution and temporary
  config files. The interruption regression uses POSIX signals and skips on Windows.
- `tests/test_compatibility.py` and `tests/public_api_1_3_0.json` — public exports,
  signatures and exit semantics pinned to the original runtime.
- `tests/test_entrypoint.py` — the standalone help/version command boundary.
- `tools/check_artifact.py` — creates a clean environment outside the checkout,
  installs the wheel, checks imports and actual command exits, and runs a copy
  of the library suite. The two invocations verify bare and declared Rich-extra
  installs without `requests`; the Rich-only rendering test skips in the bare run.

Build/test dependency installation needs package-index access; runtime smoke
operations are local and need no provider keys. Consumer-tool characterization
and scaffold tests remain in `pokanop/scripts`; they are not part of this suite.

---

## License

MIT — see [LICENSE](../LICENSE).
