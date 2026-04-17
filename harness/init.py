"""``python -m harness init`` — scaffold templates/ into a consumer project.

Copies the ``templates/`` tree (shipped with kubeharness) to ``--dest`` and
performs simple ``{{var}}`` substitution on files with the ``.tmpl`` extension:

- ``{{project_name}}``      — ``--name`` or ``basename(dest)``
- ``{{workspace_dir}}``     — ``--workspace`` (default ``workspace``)
- ``{{kubeharness_version}}`` — installed package version

After substitution the ``.tmpl`` extension is stripped. Non-template files are
copied verbatim. Existing destination files are skipped unless ``--force``.

Only the stdlib is used so ``harness init`` works before the consumer has
their YAML config in place (refactor.md §10.5).
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

    Kubeharness is installed as ``harness`` with ``templates/`` sibling to it
    (editable install and source layout). Wheel packaging (future) will need
    ``importlib.resources`` but that's out of scope for now.
    """
    root = Path(__file__).resolve().parent.parent / "templates"
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
