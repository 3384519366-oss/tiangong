"""工具集成测试 — Read/Write/Edit/Grep/Web 工具端到端验证。[原创]"""

import json
import os
import tempfile
import pytest
from pathlib import Path

# ── 工具导入（无 agent 依赖）─────────────────────────

from tiangong.core.registry import registry


def _import_tools():
    """确保工具已注册。"""
    import tiangong.tools.read_tool
    import tiangong.tools.edit_tool
    import tiangong.tools.grep_tool
    import tiangong.tools.web_tool


_import_tools()


def _dispatch(name, args):
    result = registry.dispatch(name, args or {})
    return json.loads(result)


# ── Read 工具测试 ────────────────────────────────────

class TestReadTool:
    def test_read_text_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("line 1\nline 2\nline 3\nline 4\nline 5")
            tmp = f.name
        try:
            data = _dispatch("read", {"file_path": tmp})
            assert "error" not in data
            assert data["total_lines"] == 5
            assert "line 1" in data["content"]
        finally:
            os.unlink(tmp)

    def test_read_with_offset_limit(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("a\nb\nc\nd\ne\n")
            tmp = f.name
        try:
            data = _dispatch("read", {"file_path": tmp, "offset": 2, "limit": 2})
            assert "error" not in data
            assert "b" in data["content"]
            assert "c" in data["content"]
            assert "a" not in data["content"]
        finally:
            os.unlink(tmp)

    def test_read_nonexistent_file(self):
        data = _dispatch("read", {"file_path": "/tmp/nonexistent_xyz_123.txt"})
        assert "error" in data

    def test_read_directory(self):
        data = _dispatch("read", {"file_path": "/tmp"})
        assert "error" in data or "目录" in str(data)

    def test_read_with_empty_path(self):
        data = _dispatch("read", {"file_path": ""})
        assert "error" in data


# ── Write/Edit 工具测试 ───────────────────────────────

class TestWriteTool:
    def test_write_new_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "test_write.txt")
            data = _dispatch("write", {"file_path": fpath, "content": "hello world"})
            assert "error" not in data
            assert data.get("written")
            assert Path(fpath).read_text() == "hello world"

    def test_write_overwrite(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "overwrite.txt")
            Path(fpath).write_text("old")
            data = _dispatch("write", {"file_path": fpath, "content": "new"})
            assert "error" not in data
            assert Path(fpath).read_text() == "new"

    def test_write_creates_parent_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "a", "b", "c.txt")
            data = _dispatch("write", {"file_path": fpath, "content": "nested"})
            assert "error" not in data
            assert Path(fpath).read_text() == "nested"


class TestEditTool:
    def test_edit_single_occurrence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "edit.txt")
            Path(fpath).write_text("Hello Alice, how are you?")
            data = _dispatch("edit", {
                "file_path": fpath,
                "old_string": "Alice",
                "new_string": "Bob",
            })
            assert "error" not in data
            assert data["replacements"] == 1
            assert "Bob" in Path(fpath).read_text()

    def test_edit_replace_all(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "replace.txt")
            Path(fpath).write_text("hello hello hello")
            data = _dispatch("edit", {
                "file_path": fpath,
                "old_string": "hello",
                "new_string": "hi",
                "replace_all": True,
            })
            assert "error" not in data
            assert data["replacements"] == 3
            assert Path(fpath).read_text() == "hi hi hi"

    def test_edit_duplicate_without_replace_all(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "dup.txt")
            Path(fpath).write_text("hello hello")
            data = _dispatch("edit", {
                "file_path": fpath,
                "old_string": "hello",
                "new_string": "hi",
            })
            assert "error" in data or "replace_all" in str(data).lower()

    def test_edit_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "nf.txt")
            Path(fpath).write_text("abc")
            data = _dispatch("edit", {
                "file_path": fpath,
                "old_string": "xyz",
                "new_string": "123",
            })
            assert "error" in data

    def test_edit_nonexistent_file(self):
        data = _dispatch("edit", {
            "file_path": "/tmp/ghost_file_999.txt",
            "old_string": "x",
            "new_string": "y",
        })
        assert "error" in data


# ── Grep 工具测试 ────────────────────────────────────

class TestGrepTool:
    def test_grep_find_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "test.py").write_text("import os\nimport sys\nprint('hello')")
            data = _dispatch("grep", {"pattern": "import", "path": tmpdir})
            assert "error" not in data
            assert data["count"] >= 1
            assert any("import" in r["match"] for r in data["results"])

    def test_grep_regex(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "app.py").write_text("def foo():\n    pass\ndef bar():\n    pass")
            data = _dispatch("grep", {"pattern": r"def \w+", "path": tmpdir})
            assert "error" not in data
            assert data["count"] == 2

    def test_grep_no_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "empty.py").write_text("nothing here")
            data = _dispatch("grep", {"pattern": "ZZZZNOTFOUNDZZZZ", "path": tmpdir})
            assert data["count"] == 0

    def test_grep_invalid_regex(self):
        data = _dispatch("grep", {"pattern": "["})
        assert "error" in data


# ── WebSearch 工具测试 ───────────────────────────────

class TestWebSearchTool:
    def test_search_query(self):
        """真实搜索测试（需要网络）。"""
        data = _dispatch("web_search", {"query": "Python programming"})
        # 要么返回结果，要么返回 hint（网络不可用时）
        assert "error" not in data
        assert "results" in data or "hint" in data

    def test_empty_query(self):
        data = _dispatch("web_search", {"query": ""})
        assert "error" in data

    def test_short_query(self):
        data = _dispatch("web_search", {"query": "a"})
        assert "error" in data


class TestWebFetchTool:
    def test_fetch_url(self):
        """真实抓取测试（需要网络）。"""
        data = _dispatch("web_fetch", {"url": "https://example.com"})
        assert "error" not in data
        if "content" in data:
            assert len(data["content"]) > 0

    def test_fetch_invalid_url(self):
        data = _dispatch("web_fetch", {"url": "not-a-valid-url-!!!xyz"})
        # 可能返回 error 或连接失败
        assert "error" in data or "result" in data

    def test_fetch_empty_url(self):
        data = _dispatch("web_fetch", {"url": ""})
        assert "error" in data
