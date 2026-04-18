"""Template scaffold + refresh — powers ``harness init`` and ``harness update``.

**init**: copies the bundled ``templates/`` tree into ``--dest``, stripping the
``.tmpl`` suffix and substituting:

- ``{{project_name}}``      — ``--name`` or ``basename(dest)``
- ``{{workspace_dir}}``     — ``--workspace`` (default ``workspace``)
- ``{{kubeharness_version}}`` — installed package version

Existing files are skipped unless ``--force``. ``init`` is stdlib-only so it
runs cleanly on an empty directory (refactor.md §10.5).

**update**: overwrites only harness-owned paths (agents/skills/hooks/commands,
AGENTS.md, CLAUDE.md — see :data:`HARNESS_OWNED`) and leaves user territory
(``config/``, ``context/``, ``{workspace_dir}/``, ``.claude/settings.json``)
untouched. Auto-detects ``project_name`` from the first line of ``AGENTS.md``
and ``workspace_dir`` from ``config/harness.yaml`` if PyYAML is available;
both can be overridden with ``--name`` / ``--workspace``.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


class InitError(RuntimeError):
    """Raised on unrecoverable init failure (missing templates, write denied, ...)."""


TEMPLATE_SUFFIX = ".tmpl"


@dataclass
class _Report:
    written: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    templated: list[Path] = field(default_factory=list)


def _templates_root() -> Path:
    """Resolve the bundled templates/ directory.

    ``templates/`` lives inside the ``harness`` package so wheel installs
    ship it via ``[tool.setuptools.package-data]``. Works identically for
    editable installs (source layout) and sdist/wheel installs.
    """
    root = Path(__file__).resolve().parent / "templates"
    if not root.is_dir():
        raise InitError(
            f"bundled templates/ not found at {root} "
            "(is kubeharness installed in a layout that drops the directory?)"
        )
    return root


def _kubeharness_version() -> str:
    try:
        return version("kubeharness")
    except PackageNotFoundError:
        return "0.0.0+local"


def _substitutions(
    project_name: str,
    workspace_dir: str,
) -> dict[str, str]:
    return {
        "{{project_name}}": project_name,
        "{{workspace_dir}}": workspace_dir,
        "{{kubeharness_version}}": _kubeharness_version(),
    }


def _apply_substitutions(text: str, subs: dict[str, str]) -> str:
    for k, v in subs.items():
        text = text.replace(k, v)
    return text


def _copy_one(
    src: Path,
    dst: Path,
    subs: dict[str, str],
    force: bool,
    report: _Report,
) -> None:
    is_template = dst.suffix == TEMPLATE_SUFFIX
    if is_template:
        dst = dst.with_suffix("")
    if dst.exists() and not force:
        report.skipped.append(dst)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if is_template:
        content = src.read_text(encoding="utf-8")
        content = _apply_substitutions(content, subs)
        dst.write_text(content, encoding="utf-8")
        report.templated.append(dst)
    else:
        shutil.copy2(src, dst)
        report.written.append(dst)


def _walk_copy(
    root_src: Path,
    root_dst: Path,
    subs: dict[str, str],
    force: bool,
    report: _Report,
) -> None:
    for src in root_src.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(root_src)
        dst = root_dst / rel
        _copy_one(src, dst, subs, force, report)


def run_init(
    dest: Path,
    project_name: str | None = None,
    workspace_dir: str = "workspace",
    force: bool = False,
) -> _Report:
    """Copy bundled templates into ``dest`` with ``{{var}}`` substitution.

    Prints a brief human-readable report to stdout and returns the
    :class:`_Report` for programmatic callers.
    """
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)

    if project_name is None:
        project_name = dest.name or "project"

    subs = _substitutions(project_name, workspace_dir)
    report = _Report()

    templates = _templates_root()
    _walk_copy(templates, dest, subs, force, report)

    _print_report(dest, project_name, workspace_dir, report)
    return report


def _print_report(
    dest: Path,
    project_name: str,
    workspace_dir: str,
    report: _Report,
) -> None:
    print(f"Initialized kubeharness project: {project_name}")
    print(f"  destination: {dest}")
    print(f"  workspace_dir: {workspace_dir}")
    print(f"  files written: {len(report.written)}")
    print(f"  templated:     {len(report.templated)}")
    print(f"  skipped:       {len(report.skipped)} (use --force to overwrite)")
    print()
    print("Next steps:")
    print("  1. Edit config/harness.yaml — set cluster.namespace, conventions.registry,")
    print("     and environments.* to match your cluster.")
    print(f"  2. Create {workspace_dir}/helm/<service>/ and {workspace_dir}/docker/<service>/")
    print("     for each service you plan to deploy.")
    print("  3. Review AGENTS.md and tailor the project-specific rules section.")


# ─── update ────────────────────────────────────────────────────────────────
#
# Paths owned by kubeharness (shipped verbatim, safe to overwrite on update).
# Everything else — config/**, context/**, {workspace_dir}/**, and the
# settings.json itself — is user territory and left untouched.
HARNESS_OWNED: tuple[str, ...] = (
    ".claude/agents/",
    ".claude/skills/",
    ".claude/hooks/",
    ".claude/commands/",
    "AGENTS.md",
    "CLAUDE.md",
)


def _is_harness_owned(rel: Path) -> bool:
    s = rel.as_posix()
    for entry in HARNESS_OWNED:
        if entry.endswith("/"):
            if s.startswith(entry):
                return True
        else:
            if s == entry or s == entry + TEMPLATE_SUFFIX:
                return True
    return False


def _detect_workspace_dir(dest: Path) -> str:
    yaml_path = dest / "config" / "harness.yaml"
    if not yaml_path.exists():
        return "workspace"
    try:
        import yaml  # PyYAML is a runtime dep; only imported during update.
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        ws = (data.get("conventions") or {}).get("workspace_dir")
        if isinstance(ws, str) and ws:
            return ws
    except Exception:
        pass
    return "workspace"


def _detect_project_name(dest: Path) -> str:
    agents = dest / "AGENTS.md"
    if agents.exists():
        first = agents.read_text(encoding="utf-8").splitlines()[:1]
        if first:
            line = first[0].lstrip("# ").strip()
            for suffix in (" — Agent Guide", " - Agent Guide"):
                if line.endswith(suffix):
                    return line[: -len(suffix)]
            if line:
                return line
    return dest.name or "project"


def run_update(
    dest: Path,
    project_name: str | None = None,
    workspace_dir: str | None = None,
    dry_run: bool = False,
) -> _Report:
    """Overwrite harness-owned files from the bundled templates.

    Leaves user territory (``config/**``, ``context/**``,
    ``{workspace_dir}/**``, ``.claude/settings.json``) untouched. Use this
    to pull skill/agent/hook/doc updates into an already-initialized
    project without losing local customizations.
    """
    dest = dest.resolve()
    if not dest.is_dir():
        raise InitError(f"destination does not exist: {dest}")

    if project_name is None:
        project_name = _detect_project_name(dest)
    if workspace_dir is None:
        workspace_dir = _detect_workspace_dir(dest)

    subs = _substitutions(project_name, workspace_dir)
    report = _Report()
    templates = _templates_root()

    for src in templates.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(templates)
        if not _is_harness_owned(rel):
            continue
        dst = dest / rel
        if dry_run:
            preview = dst.with_suffix("") if dst.suffix == TEMPLATE_SUFFIX else dst
            report.written.append(preview)
            continue
        _copy_one(src, dst, subs, force=True, report=report)

    _print_update_report(dest, project_name, workspace_dir, report, dry_run)
    return report


def _print_update_report(
    dest: Path,
    project_name: str,
    workspace_dir: str,
    report: _Report,
    dry_run: bool,
) -> None:
    verb = "Would update" if dry_run else "Updated"
    count = len(report.written) + len(report.templated)
    print(f"{verb} harness-owned files in: {project_name}")
    print(f"  destination:   {dest}")
    print(f"  workspace_dir: {workspace_dir}")
    print(f"  files {'to overwrite' if dry_run else 'overwritten'}: {count}")
    if dry_run:
        print()
        print("  (dry run — no files written)")
        for p in report.written[:20]:
            print(f"    {p.relative_to(dest)}")
        if len(report.written) > 20:
            print(f"    ... and {len(report.written) - 20} more")
