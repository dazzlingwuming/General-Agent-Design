from __future__ import annotations

from pathlib import Path

import getpass

from agent_harness.cli import main


def test_cli_tools_lists_builtin_tools(capsys):
    """Verify that the tools command prints the built-in tool list."""
    workspace = Path(__file__).parents[1] / "fixtures" / "demo_repo"
    code = main(["tools", "--workspace", str(workspace)])
    out = capsys.readouterr().out
    assert code == 0
    assert "list_files" in out
    assert "read_file" in out


def test_cli_code_uses_current_directory(tmp_path, monkeypatch, capsys):
    """Verify that the code command treats the current directory as workspace."""
    (tmp_path / "a.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code = main(["code", "--provider", "fake", "--task", "请分析当前目录。"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Status: COMPLETED" in out


def test_cli_exec_accepts_task_and_workspace(tmp_path, capsys):
    """Verify that the exec command accepts an explicit task and workspace."""
    (tmp_path / "a.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    code = main(["exec", "--provider", "fake", "--workspace", str(tmp_path), "--task", "请分析这个目录。"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Task ID:" in out
    assert "Status: COMPLETED" in out


def test_cli_setup_writes_user_config(tmp_path, monkeypatch, capsys):
    """Verify that setup prompts for credentials and writes user config."""
    answers = iter(["https://example.invalid", "2"])
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "secret-value")
    code = main(["setup"])
    out = capsys.readouterr().out
    assert code == 0
    assert "配置已保存" in out
    saved = tmp_path / "agent-harness" / "config.toml"
    assert saved.exists()
    assert "deepseek-v4-pro" in saved.read_text(encoding="utf-8")
