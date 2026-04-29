"""测试代码索引器: 符号提取 + 引用追踪 + 多语言支持。[原创]"""

import os
import tempfile
from pathlib import Path

import pytest

from tiangong.core.code_indexer import (
    CodeIndexer, Symbol, Reference, get_indexer,
    SYM_FUNCTION, SYM_CLASS, SYM_METHOD, SYM_VARIABLE, SYM_IMPORT, SYM_CONSTANT,
)


def _create_fixture_project(files: dict) -> str:
    """在临时目录创建测试项目文件结构。"""
    tmpdir = tempfile.mkdtemp(prefix="tiangong_idx_test_")
    for relpath, content in files.items():
        fpath = Path(tmpdir) / relpath
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content, encoding="utf-8")
    return tmpdir


class TestPythonParsing:
    def test_function_definition(self):
        proj = _create_fixture_project({
            "main.py": "def hello(name):\n    return f'Hello {name}'\n",
        })
        try:
            indexer = CodeIndexer(proj)
            indexer.index()
            syms = indexer.find_symbols("hello")
            assert len(syms) >= 1
            assert syms[0]["kind"] == SYM_FUNCTION
            assert "name" in syms[0]["signature"]
        finally:
            import shutil; shutil.rmtree(proj, ignore_errors=True)

    def test_class_definition(self):
        proj = _create_fixture_project({
            "models.py": "class User:\n    def __init__(self, name):\n        self.name = name\n",
        })
        try:
            indexer = CodeIndexer(proj)
            indexer.index()
            syms = indexer.find_symbols("User")
            assert len(syms) >= 1
            assert syms[0]["kind"] == SYM_CLASS

            method_syms = indexer.find_symbols("__init__")
            assert len(method_syms) >= 1
            assert method_syms[0]["kind"] == SYM_METHOD
        finally:
            import shutil; shutil.rmtree(proj, ignore_errors=True)

    def test_variable_and_constant(self):
        proj = _create_fixture_project({
            "config.py": "DEBUG = True\nMAX_SIZE = 1024\n_version = '1.0'\n",
        })
        try:
            indexer = CodeIndexer(proj)
            indexer.index()

            # 全大写是常量
            debug_syms = indexer.find_symbols("DEBUG")
            assert len(debug_syms) >= 1
            assert debug_syms[0]["kind"] == SYM_CONSTANT

            max_syms = indexer.find_symbols("MAX_SIZE")
            assert len(max_syms) >= 1
            assert max_syms[0]["kind"] == SYM_CONSTANT
        finally:
            import shutil; shutil.rmtree(proj, ignore_errors=True)

    def test_import_tracking(self):
        proj = _create_fixture_project({
            "app.py": "import os\nfrom pathlib import Path\nfrom typing import List, Dict\n",
        })
        try:
            indexer = CodeIndexer(proj)
            indexer.index()

            os_syms = indexer.find_symbols("os")
            assert len(os_syms) >= 1
            assert os_syms[0]["kind"] == SYM_IMPORT

            path_syms = indexer.find_symbols("Path")
            assert len(path_syms) >= 1
        finally:
            import shutil; shutil.rmtree(proj, ignore_errors=True)

    def test_async_function(self):
        proj = _create_fixture_project({
            "async_app.py": "async def fetch_data(url):\n    return await something(url)\n",
        })
        try:
            indexer = CodeIndexer(proj)
            indexer.index()
            syms = indexer.find_symbols("fetch_data")
            assert len(syms) >= 1
            assert "async" in syms[0]["signature"].lower()
        finally:
            import shutil; shutil.rmtree(proj, ignore_errors=True)


class TestCrossFileReferences:
    def test_basic_reference(self):
        proj = _create_fixture_project({
            "lib.py": "def greet(name):\n    return f'Hi {name}'\n",
            "main.py": "from lib import greet\n\nif __name__ == '__main__':\n    print(greet('world'))\n",
        })
        try:
            indexer = CodeIndexer(proj)
            indexer.index()
            refs = indexer.find_references("greet")
            # main.py 中应该引用了 greet
            assert len(refs) >= 1, f"期望至少1个引用，实际 {len(refs)}"
            assert any("main.py" in r["file"] for r in refs)
        finally:
            import shutil; shutil.rmtree(proj, ignore_errors=True)


class TestFileSymbols:
    def test_multi_symbol_file(self):
        proj = _create_fixture_project({
            "module.py": (
                "import json\n\n"
                "class Parser:\n"
                "    def parse(self, data):\n"
                "        return json.loads(data)\n\n"
                "def main():\n"
                "    p = Parser()\n"
                "    return p.parse('{}')\n"
            ),
        })
        try:
            indexer = CodeIndexer(proj)
            indexer.index()
            syms = indexer.get_file_symbols(str(Path(proj) / "module.py"))
            names = {s["name"] for s in syms}
            assert "json" in names
            assert "Parser" in names
            assert "main" in names
            assert "parse" in names
        finally:
            import shutil; shutil.rmtree(proj, ignore_errors=True)


class TestIncrementalIndex:
    def test_incremental_skip_unchanged(self):
        proj = _create_fixture_project({
            "a.py": "def foo():\n    pass\n",
            "b.py": "def bar():\n    pass\n",
        })
        try:
            indexer = CodeIndexer(proj)
            r1 = indexer.index()
            assert r1["indexed"] == 2

            # 再次索引，应该跳过
            r2 = indexer.index()
            assert r2["indexed"] == 0

            # 修改文件
            (Path(proj) / "a.py").write_text("def foo():\n    return 42\n")
            r3 = indexer.index()
            assert r3["indexed"] == 1
        finally:
            import shutil; shutil.rmtree(proj, ignore_errors=True)


class TestSearchCodebase:
    def test_fulltext_search(self):
        proj = _create_fixture_project({
            "x.py": "CONNECTION_STRING = 'postgresql://localhost/db'\n",
            "y.py": "API_KEY = 'sk-secret-12345'\n",
        })
        try:
            indexer = CodeIndexer(proj)
            indexer.index()
            results = indexer.search_codebase("CONNECTION_STRING")
            assert any("CONNECTION_STRING" in r["content"] for r in results)
        finally:
            import shutil; shutil.rmtree(proj, ignore_errors=True)


class TestMultiLanguage:
    def test_javascript_parsing(self):
        proj = _create_fixture_project({
            "app.js": (
                "function initApp() {\n"
                "  const config = { debug: true };\n"
                "  return config;\n"
                "}\n\n"
                "class Component {\n"
                "  render() {\n"
                "    return '<div></div>';\n"
                "  }\n"
                "}\n"
            ),
        })
        try:
            indexer = CodeIndexer(proj)
            indexer.index()

            syms = indexer.find_symbols("initApp")
            assert len(syms) >= 1

            syms = indexer.find_symbols("Component")
            assert len(syms) >= 1
        finally:
            import shutil; shutil.rmtree(proj, ignore_errors=True)

    def test_typescript_interface(self):
        proj = _create_fixture_project({
            "types.ts": (
                "export interface User {\n"
                "  id: string;\n"
                "  name: string;\n"
                "}\n\n"
                "export async function getUser(id: string): Promise<User> {\n"
                "  return { id, name: 'test' };\n"
                "}\n"
            ),
        })
        try:
            indexer = CodeIndexer(proj)
            indexer.index()

            syms = indexer.find_symbols("User")
            assert len(syms) >= 1

            syms = indexer.find_symbols("getUser")
            assert len(syms) >= 1
        finally:
            import shutil; shutil.rmtree(proj, ignore_errors=True)


class TestSymbolKindFilter:
    def test_filter_by_kind(self):
        proj = _create_fixture_project({
            "mixed.py": (
                "class MyClass:\n"
                "    def method(self):\n"
                "        pass\n\n"
                "def my_function():\n"
                "    pass\n"
            ),
        })
        try:
            indexer = CodeIndexer(proj)
            indexer.index()

            # 只查 class
            classes = indexer.find_symbols("My", kind=SYM_CLASS)
            assert all(s["kind"] == SYM_CLASS for s in classes)

            # 只查 function
            funcs = indexer.find_symbols("my_", kind=SYM_FUNCTION)
            assert all(s["kind"] == SYM_FUNCTION for s in funcs)
        finally:
            import shutil; shutil.rmtree(proj, ignore_errors=True)
