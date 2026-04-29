"""测试 Code Agent: AST 安全验证 + 代码块提取。[借鉴smolagents]"""

import pytest
from tiangong.core.code_agent import (
    validate_code,
    extract_code_blocks,
    has_code_blocks,
    CodeSecurityValidator,
    _ALLOWED_IMPORTS,
    _BLOCKED_IMPORTS,
    _BLOCKED_FUNCTIONS,
)


class TestValidateCode:
    def test_safe_code(self):
        safe, errors = validate_code("x = 1 + 1\nprint(x)")
        assert safe
        assert errors == []

    def test_safe_import(self):
        safe, errors = validate_code("import json\nimport math")
        assert safe

    def test_blocked_os_import(self):
        safe, errors = validate_code("import os")
        assert not safe
        assert any("os" in e for e in errors)

    def test_blocked_sys_import(self):
        safe, errors = validate_code("import sys")
        assert not safe

    def test_blocked_subprocess_import(self):
        safe, errors = validate_code("import subprocess")
        assert not safe

    def test_blocked_eval(self):
        safe, errors = validate_code("eval('1+1')")
        assert not safe

    def test_blocked_exec(self):
        safe, errors = validate_code("exec('x=1')")
        assert not safe

    def test_blocked_open(self):
        safe, errors = validate_code("open('/etc/passwd')")
        assert not safe

    def test_blocked_dunder(self):
        safe, errors = validate_code("x.__class__")
        assert not safe

    def test_blocked_compile(self):
        safe, errors = validate_code("compile('x=1', '', 'exec')")
        assert not safe

    def test_syntax_error(self):
        safe, errors = validate_code("if True print('missing colon')")
        assert not safe
        assert any("语法错误" in e for e in errors)

    def test_allowed_tools_dont_flag(self):
        """工具名不应被标记为危险。"""
        safe, errors = validate_code(
            "bash(command='ls')\nmemory(query='test')",
            allowed_tools={"bash", "memory"}
        )
        # bash/memory 不在 _BLOCKED_FUNCTIONS 中，但作为 Name 被允许
        assert safe

    def test_blocked_ctypes(self):
        safe, errors = validate_code("import ctypes")
        assert not safe

    def test_blocked_socket(self):
        safe, errors = validate_code("import socket")
        assert not safe


class TestExtractCodeBlocks:
    def test_python_block(self):
        text = """Here is some code:
```python
print("hello")
```
"""
        blocks = extract_code_blocks(text)
        assert len(blocks) == 1
        assert blocks[0][0] == "python"
        assert "print" in blocks[0][1]

    def test_multiple_blocks(self):
        text = """```python
a = 1
```
Some text
```python
b = 2
```
"""
        blocks = extract_code_blocks(text)
        assert len(blocks) == 2

    def test_no_code_blocks(self):
        blocks = extract_code_blocks("Just plain text, no code.")
        assert blocks == []

    def test_non_python_block_ignored(self):
        text = """```javascript
console.log("hi");
```
"""
        blocks = extract_code_blocks(text)
        assert blocks == []


class TestHasCodeBlocks:
    def test_has_python(self):
        assert has_code_blocks("```python\nx=1\n```")

    def test_no_code(self):
        assert not has_code_blocks("Just text")

    def test_has_py_short(self):
        assert has_code_blocks("```py\nx=1\n```")
