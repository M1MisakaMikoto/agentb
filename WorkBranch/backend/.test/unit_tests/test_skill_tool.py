import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BACKEND_DIR)

import singleton
from service.agent_service.tools import skill_tool
from service.agent_service.tools.registry import ALL_TOOLS
from service.agent_service.graph.subgraphs.tool_registry import get_allowed_tools


SKILLS_DIR = skill_tool._WORKBRANCH_DIR / "skills"
# 测试用临时目录放在仓库 .temp/（已 gitignore），避免依赖系统临时目录权限
TMP_ROOT = Path(BACKEND_DIR).parents[1] / ".temp" / "skill_tool_tests"


@pytest.fixture
def skills_tmp_dir():
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(dir=str(TMP_ROOT)))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class _FakeSettings:
    def __init__(self, skills_dir):
        self._skills_dir = str(skills_dir)

    def get(self, key):
        assert key == "agent_tools:skills:dir", f"unexpected settings key: {key}"
        return self._skills_dir


def _use_skills_dir(monkeypatch, skills_dir):
    monkeypatch.setattr(
        singleton, "get_settings_service", lambda: _FakeSettings(skills_dir)
    )


def test_skill_list_returns_shipped_skills(monkeypatch):
    _use_skills_dir(monkeypatch, SKILLS_DIR)

    result = skill_tool.execute_skill({"operation": "list"})

    assert result["error"] is None
    assert "region-database-rules" in result["result"]
    assert "report-format" in result["result"]
    assert "必读" in result["result"]


def test_skill_read_returns_full_body_without_truncation(monkeypatch):
    _use_skills_dir(monkeypatch, SKILLS_DIR)

    raw = (SKILLS_DIR / "report-format" / "SKILL.md").read_text(encoding="utf-8")
    expected_body = raw.split("\n---", 1)[1].lstrip("\n")

    result = skill_tool.execute_skill({"operation": "read", "name": "report-format"})

    assert result["error"] is None
    assert expected_body in result["result"]
    last_line = expected_body.rstrip("\n").splitlines()[-1]
    assert last_line in result["result"]


def test_skill_read_region_rules_contain_migrated_content(monkeypatch):
    _use_skills_dir(monkeypatch, SKILLS_DIR)

    result = skill_tool.execute_skill(
        {"operation": "read", "name": "region-database-rules"}
    )

    assert result["error"] is None
    assert "TB_Market" in result["result"]
    assert "禁止直接用地区名称" in result["result"]


def test_skill_read_unknown_name_reports_available_skills(monkeypatch):
    _use_skills_dir(monkeypatch, SKILLS_DIR)

    result = skill_tool.execute_skill({"operation": "read", "name": "not-exist"})

    assert result["result"] is None
    assert "技能不存在：not-exist" in result["error"]
    assert "report-format" in result["error"]


def test_skill_rejects_invalid_operation(monkeypatch):
    _use_skills_dir(monkeypatch, SKILLS_DIR)

    result = skill_tool.execute_skill({"operation": "delete"})

    assert result["result"] is None
    assert "无效的 operation" in result["error"]


def test_skill_reports_missing_skills_dir(monkeypatch, skills_tmp_dir):
    _use_skills_dir(monkeypatch, skills_tmp_dir / "no-such-dir")

    result = skill_tool.execute_skill({"operation": "list"})

    assert result["result"] is None
    assert "技能读取失败" in result["error"]
    assert "技能目录不存在" in result["error"]


def test_skill_file_requires_description(monkeypatch, skills_tmp_dir):
    bad_skill = skills_tmp_dir / "bad-skill"
    bad_skill.mkdir()
    (bad_skill / "SKILL.md").write_text(
        "---\nname: bad-skill\n---\n\n正文\n", encoding="utf-8"
    )
    _use_skills_dir(monkeypatch, skills_tmp_dir)

    result = skill_tool.execute_skill({"operation": "list"})

    assert result["result"] is None
    assert "description" in result["error"]


def test_skill_file_requires_matching_directory_name(monkeypatch, skills_tmp_dir):
    bad_skill = skills_tmp_dir / "some-skill"
    bad_skill.mkdir()
    (bad_skill / "SKILL.md").write_text(
        "---\nname: other-skill\ndescription: 用途\n---\n\n正文\n", encoding="utf-8"
    )
    _use_skills_dir(monkeypatch, skills_tmp_dir)

    result = skill_tool.execute_skill({"operation": "list"})

    assert result["result"] is None
    assert "与目录名" in result["error"]


def test_skill_tool_registered_and_allowed_for_all_agents():
    assert "skill" in ALL_TOOLS
    assert ALL_TOOLS["skill"]["params"].startswith('skill:{"operation"')

    for agent_type in (
        "director_agent",
        "prediction_agent",
        "explore_agent",
        "review_agent",
        "plan_agent",
    ):
        assert "skill" in get_allowed_tools(agent_type), agent_type
