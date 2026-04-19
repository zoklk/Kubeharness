"""Tests for harness/init.py — template copy + {{var}} substitution.

We swap the bundled templates/ root for a throwaway tree built inside tmp_path
so tests are independent of whatever is (or isn't) shipped on disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness import init as init_mod


@pytest.fixture
def fake_templates(tmp_path: Path, monkeypatch) -> Path:
    """Build a minimal fake templates/ root and point init.py at it."""
    root = tmp_path / "templates"
    (root / "config").mkdir(parents=True)
    (root / ".claude").mkdir(parents=True)

    # a templated file (with .tmpl suffix)
    (root / "AGENTS.md.tmpl").write_text(
        "# {{project_name}}\nworkspace={{workspace_dir}} v={{kubeharness_version}}\n",
        encoding="utf-8",
    )
    # nested templated file
    (root / "config" / "harness.yaml.example.tmpl").write_text(
        "workspace_dir: {{workspace_dir}}\n",
        encoding="utf-8",
    )
    # verbatim (no .tmpl)
    (root / ".claude" / "settings.json").write_text('{"k": 1}\n', encoding="utf-8")

    monkeypatch.setattr(init_mod, "_templates_root", lambda: root)
    return root


def test_init_writes_files_and_substitutes(fake_templates, tmp_path: Path, capsys):
    dest = tmp_path / "project"
    report = init_mod.run_init(
        dest=dest,
        project_name="my-proj",
        workspace_dir="ws",
        force=False,
    )
    # templated files: .tmpl stripped, vars substituted
    agents = dest / "AGENTS.md"
    assert agents.exists()
    text = agents.read_text()
    assert "# my-proj" in text
    assert "workspace=ws" in text
    assert "{{" not in text  # all placeholders replaced

    yaml_ex = dest / "config" / "harness.yaml.example"
    assert yaml_ex.exists()
    assert "workspace_dir: ws" in yaml_ex.read_text()

    # verbatim file copied as-is
    settings = dest / ".claude" / "settings.json"
    assert settings.exists()
    assert settings.read_text() == '{"k": 1}\n'

    # report counts
    assert agents in report.templated
    assert settings in report.written
    assert report.skipped == []

    # print report went to stdout
    out = capsys.readouterr().out
    assert "my-proj" in out
    assert "Next steps" in out


def test_init_skips_existing_without_force(fake_templates, tmp_path: Path):
    dest = tmp_path / "project"
    (dest / ".claude").mkdir(parents=True)
    (dest / ".claude" / "settings.json").write_text("preexisting", encoding="utf-8")

    report = init_mod.run_init(dest=dest, project_name="p", workspace_dir="w", force=False)

    # preexisting file was left untouched
    assert (dest / ".claude" / "settings.json").read_text() == "preexisting"
    assert (dest / ".claude" / "settings.json") in report.skipped


def test_init_force_overwrites(fake_templates, tmp_path: Path):
    dest = tmp_path / "project"
    (dest / ".claude").mkdir(parents=True)
    (dest / ".claude" / "settings.json").write_text("preexisting", encoding="utf-8")

    report = init_mod.run_init(dest=dest, project_name="p", workspace_dir="w", force=True)
    assert (dest / ".claude" / "settings.json").read_text() == '{"k": 1}\n'
    assert (dest / ".claude" / "settings.json") in report.written


def test_init_default_project_name_is_basename(fake_templates, tmp_path: Path):
    dest = tmp_path / "some-project"
    init_mod.run_init(dest=dest, project_name=None, workspace_dir="w", force=False)
    assert "# some-project" in (dest / "AGENTS.md").read_text()


def test_init_raises_when_templates_missing(monkeypatch, tmp_path: Path):
    def _no_templates():
        raise init_mod.InitError("bundled templates/ not found at /nope")
    monkeypatch.setattr(init_mod, "_templates_root", _no_templates)
    with pytest.raises(init_mod.InitError, match="not found"):
        init_mod.run_init(dest=tmp_path / "x", project_name="p", workspace_dir="w")
