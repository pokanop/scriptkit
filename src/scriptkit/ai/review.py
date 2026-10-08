"""Proposal review composes deterministic rendering, static conformance and transactions."""

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from scriptkit.conformance.static import validate
from scriptkit.contracts import ToolSpec
from scriptkit.generator.plan import Change, Plan, preview, read, target
from scriptkit.generator.render import sha256
from scriptkit.generator.scaffolds import existing, tool
from scriptkit.generator.transaction import Writer, apply as apply_plan
from .context import select
from .contracts import Context, Proposal, parse_response


@dataclass(frozen=True)
class Review:
    plan: Plan
    proposal: Proposal

    @property
    def approval_hash(self) -> str:
        # Bind approval to the envelope AND every checked byte, including unchanged files.
        material = self.proposal.canonical_json() + self.plan.to_json()
        for path, data in self.plan.bases:
            material += path + ":" + (sha256(data) if data is not None else "absent") + "\n"
        return sha256(material.encode())

    def to_json(self) -> str:
        from scriptkit.generator.render import canonical
        import json

        return canonical(
            {
                "approval_hash": self.approval_hash,
                "plan": json.loads(self.plan.to_json()),
                "dependency_suggestions": self.proposal.dependency_suggestions,
                "warning": "Inference is nondeterministic. Static checks are not a security proof. No code has run.",
            }
        ).decode()


def review(root: Path, context: Context, proposal: Proposal) -> Review:
    """Read-only against root. Scratch data is never imported, installed or executed."""
    proposal = parse_response(proposal.canonical_json().encode())
    current = select(root, tuple(f.path for f in context.files))
    if context != current or proposal.context_hash != current.identity:
        raise ValueError("stale or mismatched context")
    spec, layout = existing(root)
    value = spec.to_dict()
    delta = proposal.spec_delta
    if delta.description is not None:
        value["description"] = delta.description
    commands = {c["name"]: c for c in value["commands"]}
    for change in delta.command_help:
        if change.name not in commands:
            raise ValueError("spec delta may only describe existing commands")
        commands[change.name]["help"] = change.help
    generated = preview(root, tool(ToolSpec.from_dict(value), layout))
    if generated.conflicts:
        raise ValueError("generated drift: reconcile manually before AI review")
    bases = dict(generated.bases)
    changes = {c.path: c for c in generated.changes}
    selected = {f.path: f for f in current.files}
    for patch in proposal.patches:
        if patch.path not in selected:
            raise ValueError("patch is outside explicitly selected extensions")
        selected_file = selected[patch.path]
        if patch.base_hash != selected_file.base_hash:
            raise ValueError("stale patch base hash")
        before = selected_file.content.encode() if selected_file.content is not None else None
        bases[patch.path] = before
        after = patch.content.encode()
        if before != after:
            changes[patch.path] = Change(patch.path, before, after)

    # Snapshot additional handwritten modules for conformance and approval binding.
    # Walk without following links, checking each directory as a confined target.
    def source_files(directory: Path) -> None:
        for file in sorted(directory.iterdir()):
            name = file.relative_to(root).as_posix()
            target(root, name + "/sentinel" if file.is_dir() else name)
            if file.is_dir():
                source_files(file)
            elif file.suffix == ".py":
                if file.stat().st_size > 65536:
                    raise ValueError("static source budget exceeded")
                bases[name] = read(root, name)
            if len(bases) > 256:
                raise ValueError("static file budget exceeded")

    source_files(root / "src")
    if sum(len(b or b"") for b in bases.values()) > 2 * 1024 * 1024:
        raise ValueError("static project budget exceeded")
    candidate = dict(bases)
    candidate.update({p: c.after for p, c in changes.items()})
    with TemporaryDirectory(prefix="scriptkit-ai-static-") as temporary:
        stage = Path(temporary)
        for name, data in candidate.items():
            if data is None:
                continue
            file = target(stage, name)
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(data)
        # Use the same static syntax/import rules for proposed tests, not a second scanner.
        for patch in proposal.patches:
            if patch.path.startswith("tests/"):
                file = stage / "src" / "_ai_test_checks" / Path(patch.path).name
                file.parent.mkdir(exist_ok=True)
                file.write_text(patch.content, encoding="utf-8")
        report = validate(stage)
        if not report.valid:
            raise ValueError(f"static checks rejected proposal: {report.to_data()}")
    return Review(
        Plan(tuple(changes[p] for p in sorted(changes)), tuple(sorted(bases.items()))), proposal
    )


def apply(
    root: Path,
    context: Context,
    proposal: Proposal,
    *,
    approved_review_hash: str,
    writer: Writer | None = None,
) -> None:
    """Recheck at explicit apply; journal/recovery semantics are the generator's.

    No run/test option exists here: proposed Python never executes on the host.
    """
    checked = review(root, context, proposal)
    if approved_review_hash != checked.approval_hash:
        raise ValueError("explicit approval of the exact review is required")
    apply_plan(root, checked.plan, writer=writer)
