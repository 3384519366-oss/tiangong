"""代码库索引器 — 多语言符号提取 + 跨文件引用图 + 文本搜索。[原创]

纯本地计算，零 API 依赖:
1. 符号索引: Python 内置 ast / 正则表达式 → 函数/类/变量/导入定义
2. 引用追踪: 跨文件调用图 → rename/propagate/impact analysis
3. 文本搜索: 全文 grep 级别搜索代码内容
4. 可扩展: 如需语义搜索，可接入本地 SentenceTransformer / bm25s

支持语言:
- Python: 内置 ast (完整)
- JavaScript/TypeScript/Go/Rust/Java/Kotlin/PHP/Ruby/C/C++: 正则匹配
- 其他: 通用符号模式匹配
"""

import ast as py_ast
import json
import logging
import os
import re
import time
from pathlib import Path
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ── 忽略目录 ──────────────────────────────────────────

_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".tox",
    "dist", "build", ".next", ".nuxt", ".cache", ".idea", ".vscode",
    "target", ".mypy_cache", ".ruff_cache", ".pytest_cache",
    "*.egg-info", "bower_components", ".turbo", ".angular",
    "coverage", "htmlcov", ".coverage*",
}

# ── 语言识别 ──────────────────────────────────────────

_LANG_MAP = {
    ".py": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".rb": "ruby",
    ".swift": "swift",
    ".kt": "kotlin", ".kts": "kotlin",
    ".scala": "scala",
    ".php": "php",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".sql": "sql",
    ".vue": "vue", ".svelte": "svelte",
    ".tf": "hcl", ".tfvars": "hcl",
}

# ── 符号类型 ──────────────────────────────────────────

SYM_FUNCTION = "function"
SYM_CLASS = "class"
SYM_METHOD = "method"
SYM_VARIABLE = "variable"
SYM_IMPORT = "import"
SYM_CONSTANT = "constant"
SYM_INTERFACE = "interface"
SYM_TYPE = "type"


class Symbol:
    """代码符号。"""
    __slots__ = ("name", "kind", "file", "line", "col",
                 "signature", "docstring", "parent", "visibility")

    def __init__(self, name: str, kind: str, file: str, line: int, col: int = 0):
        self.name = name
        self.kind = kind
        self.file = file
        self.line = line
        self.col = col
        self.signature: str = ""
        self.docstring: str = ""
        self.parent: str = ""  # 父类/模块名
        self.visibility: str = "public"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "file": self.file,
            "line": self.line,
            "col": self.col,
            "signature": self.signature,
            "docstring": self.docstring[:200] if self.docstring else "",
            "parent": self.parent,
            "visibility": self.visibility,
        }


class Reference:
    """符号引用。"""
    __slots__ = ("symbol_name", "file", "line", "context")

    def __init__(self, symbol_name: str, file: str, line: int, context: str = ""):
        self.symbol_name = symbol_name
        self.file = file
        self.line = line
        self.context = context[:200]


class CodeIndexer:
    """代码库索引器 — 增量索引 + 符号查询 + 引用追踪。[原创]"""

    def __init__(self, root_dir: str = None):
        self.root = Path(root_dir or os.getcwd()).resolve()
        self._symbols: Dict[str, List[Symbol]] = defaultdict(list)
        self._references: Dict[str, List[Reference]] = defaultdict(list)
        self._file_symbols: Dict[str, List[str]] = defaultdict(list)
        self._indexed_files: Set[str] = set()
        self._index_time: float = 0
        self._file_mtimes: Dict[str, float] = {}
        self._stats = {"files": 0, "symbols": 0, "references": 0, "lines": 0}

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    # ── 索引构建 ──────────────────────────────────────

    def index(self, force: bool = False) -> dict:
        """全量或增量索引代码库。"""
        t0 = time.time()

        files = self._collect_files()
        new_files = [f for f in files if self._needs_reindex(f)] if not force else files

        if not new_files and self._indexed_files:
            logger.info("代码索引已是最新 (%d 文件)", len(self._indexed_files))
            return {"indexed": 0, "total": len(self._indexed_files), "stats": self._stats}

        self._stats["files"] = 0
        self._stats["symbols"] = 0
        self._stats["references"] = 0
        self._stats["lines"] = 0

        for file_path in new_files:
            try:
                self._index_file(file_path)
                self._stats["files"] += 1
            except Exception as e:
                logger.debug("索引失败 %s: %s", file_path, e)

        # 构建引用图
        self._build_reference_graph(new_files)

        self._index_time = time.time() - t0
        logger.info(
            "代码索引完成: %d 文件, %d 符号, %d 引用 (%.1fs)",
            self._stats["files"], self._stats["symbols"],
            self._stats["references"], self._index_time,
        )
        return {
            "indexed": len(new_files),
            "total": len(self._indexed_files),
            "stats": self._stats,
            "duration_ms": int(self._index_time * 1000),
        }

    def _collect_files(self) -> List[Path]:
        """收集所有需要索引的源文件。"""
        files = []
        for item in self.root.rglob("*"):
            if item.is_dir():
                if item.name in _IGNORE_DIRS or item.name.startswith("."):
                    continue
                continue
            if item.suffix.lower() in _LANG_MAP:
                if not item.name.startswith("."):
                    files.append(item)
        return files

    def _needs_reindex(self, file_path: Path) -> bool:
        """检查文件是否需要重新索引。"""
        fp = str(file_path)
        if fp not in self._indexed_files:
            return True
        old_mtime = self._file_mtimes.get(fp, 0)
        try:
            new_mtime = file_path.stat().st_mtime
            return new_mtime > old_mtime
        except OSError:
            return True

    # ── 单文件解析 ────────────────────────────────────

    def _index_file(self, file_path: Path):
        """解析单个文件并注册符号。"""
        fp = str(file_path)
        lang = _LANG_MAP.get(file_path.suffix.lower(), "unknown")

        # 清除该文件的旧符号
        old_symbols = self._file_symbols.pop(fp, [])
        for name in old_symbols:
            self._symbols[name] = [s for s in self._symbols.get(name, []) if s.file != fp]

        # 清理旧引用
        for name in list(self._references.keys()):
            self._references[name] = [r for r in self._references[name] if r.file != fp]

        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            return

        self._stats["lines"] += content.count("\n") + 1
        self._file_mtimes[fp] = file_path.stat().st_mtime

        symbols = []

        if lang == "python":
            symbols = self._parse_python(fp, content)
        else:
            symbols = self._parse_generic(fp, content, lang)

        for sym in symbols:
            self._symbols[sym.name].append(sym)
            self._file_symbols[fp].append(sym.name)
            self._stats["symbols"] += 1

        self._indexed_files.add(fp)

    # ── Python AST 解析 ───────────────────────────────

    def _parse_python(self, file_path: str, content: str) -> List[Symbol]:
        """用内置 ast 模块解析 Python 文件。"""
        symbols = []
        try:
            tree = py_ast.parse(content, filename=file_path)
        except SyntaxError:
            return symbols

        for node in py_ast.walk(tree):
            sym = None

            if isinstance(node, py_ast.FunctionDef):
                sym = Symbol(node.name, SYM_FUNCTION, file_path, node.lineno, node.col_offset)
                sym.signature = self._py_function_sig(node)
                if node.body and isinstance(node.body[0], py_ast.Expr) and isinstance(node.body[0].value, (py_ast.Constant, py_ast.Str)):
                    sym.docstring = (node.body[0].value.value or node.body[0].value.s or "")[:200]
                # 检查是否是方法（在类内部）
                for parent in py_ast.walk(tree):
                    if isinstance(parent, py_ast.ClassDef) and node in py_ast.walk(parent):
                        sym.kind = SYM_METHOD
                        sym.parent = parent.name
                        break

            elif isinstance(node, py_ast.ClassDef):
                sym = Symbol(node.name, SYM_CLASS, file_path, node.lineno, node.col_offset)
                sym.signature = f"class {node.name}({', '.join(self._get_base_names(node))})"
                if node.body and isinstance(node.body[0], py_ast.Expr) and isinstance(node.body[0].value, (py_ast.Constant, py_ast.Str)):
                    sym.docstring = (node.body[0].value.value or node.body[0].value.s or "")[:200]

            elif isinstance(node, py_ast.AsyncFunctionDef):
                sym = Symbol(node.name, SYM_FUNCTION, file_path, node.lineno, node.col_offset)
                sym.signature = f"async {self._py_function_sig(node)}"

            # 顶层变量赋值
            elif isinstance(node, py_ast.Assign) and node.col_offset == 0:
                for target in node.targets:
                    if isinstance(target, py_ast.Name):
                        # 全大写 → 常量, 否则 → 变量
                        kind = SYM_CONSTANT if target.id.isupper() else SYM_VARIABLE
                        s = Symbol(target.id, kind, file_path, node.lineno, node.col_offset)
                        try:
                            s.signature = py_ast.unparse(node.value) if hasattr(py_ast, 'unparse') else ""
                        except Exception:
                            pass
                        symbols.append(s)

            # 导入
            elif isinstance(node, py_ast.Import):
                for alias in node.names:
                    s = Symbol(alias.name, SYM_IMPORT, file_path, node.lineno, node.col_offset)
                    s.signature = f"import {alias.name}"
                    if alias.asname:
                        s.signature += f" as {alias.asname}"
                    symbols.append(s)

            elif isinstance(node, py_ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    s = Symbol(alias.name, SYM_IMPORT, file_path, node.lineno, node.col_offset)
                    s.signature = f"from {module} import {alias.name}"
                    if alias.asname:
                        s.signature += f" as {alias.asname}"
                    symbols.append(s)

            if sym:
                symbols.append(sym)

        return symbols

    @staticmethod
    def _py_function_sig(node) -> str:
        """提取 Python 函数签名。"""
        args = []
        for a in node.args.args:
            arg_str = a.arg
            if a.annotation:
                try:
                    ann = py_ast.unparse(a.annotation) if hasattr(py_ast, 'unparse') else ""
                    arg_str += f": {ann}"
                except Exception:
                    pass
            args.append(arg_str)
        sig = f"def {node.name}({', '.join(args)})"
        if node.returns:
            try:
                sig += f" -> {py_ast.unparse(node.returns)}" if hasattr(py_ast, 'unparse') else ""
            except Exception:
                pass
        return sig

    @staticmethod
    def _get_base_names(node) -> List[str]:
        """提取类继承的基类名称。"""
        names = []
        for base in node.bases:
            if isinstance(base, py_ast.Name):
                names.append(base.id)
            elif isinstance(base, py_ast.Attribute):
                names.append(base.attr)
        return names

    # ── 通用正则解析 ──────────────────────────────────

    def _parse_generic(self, file_path: str, content: str, lang: str) -> List[Symbol]:
        """正则回退：匹配常见语言的函数/类/变量定义模式。"""
        symbols = []

        # 函数定义: def/function/fn/func
        func_patterns = [
            (r'^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(', ["javascript", "typescript"]),
            (r'^(?:export\s+)?(?:async\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(', ["javascript", "typescript"]),
            (r'^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(', ["go"]),
            (r'^(?:pub(?:\s*\(\s*\w+\s*\))?\s+)?fn\s+(\w+)\s*[<(]', ["rust"]),
            (r'^\s*(?:public|private|protected|static)?\s*(?:[\w<>\[\]]+\s+)+(\w+)\s*\(', ["java", "kotlin", "scala"]),
            (r'^def\s+(\w+)', ["ruby"]),
            (r'^(?:public\s+)?function\s+(\w+)\s*\(', ["php"]),
        ]

        for pattern, langs in func_patterns:
            if lang in langs:
                for m in re.finditer(pattern, content, re.MULTILINE):
                    sym = Symbol(m.group(1), SYM_FUNCTION, file_path,
                                content[:m.start()].count("\n") + 1)
                    sym.signature = m.group(0).strip()[:150]
                    symbols.append(sym)

        # 类定义: class/struct/interface
        class_patterns = [
            (r'^(?:export\s+)?(?:abstract\s+)?class\s+(\w+)', ["javascript", "typescript", "java", "kotlin", "scala", "php"]),
            (r'^type\s+(\w+)\s+struct', ["go"]),
            (r'^(?:pub\s+)?struct\s+(\w+)', ["rust"]),
            (r'^(?:pub\s+)?trait\s+(\w+)', ["rust"]),
            (r'^(?:public\s+)?interface\s+(\w+)', ["java", "typescript"]),
            (r'^class\s+(\w+)', ["ruby"]),
        ]

        for pattern, langs in class_patterns:
            if lang in langs:
                for m in re.finditer(pattern, content, re.MULTILINE):
                    kind = SYM_INTERFACE if "interface" in m.group(0) or "trait" in m.group(0) else SYM_CLASS
                    sym = Symbol(m.group(1), kind, file_path,
                                content[:m.start()].count("\n") + 1)
                    sym.signature = m.group(0).strip()[:150]
                    symbols.append(sym)

        # 导入语句
        import_patterns = [
            (r'^import\s+.*?from\s+[\'"]([^\'"]+)[\'"]', ["javascript", "typescript"]),
            (r'^require\s*\(\s*[\'"]([^\'"]+)[\'"]', ["javascript"]),
            (r'^import\s+\(\s*\n?((?:\s+[^\n]+\n?)*?)\)', ["go"]),
            (r'^use\s+(\w+)', ["rust"]),
            (r'^#include\s+[<"]([^>"]+)[>"]', ["c", "cpp"]),
        ]

        for pattern, langs in import_patterns:
            if lang in langs:
                for m in re.finditer(pattern, content, re.MULTILINE):
                    sym = Symbol(m.group(1), SYM_IMPORT, file_path,
                                content[:m.start()].count("\n") + 1)
                    sym.signature = m.group(0).strip()[:150]
                    symbols.append(sym)

        return symbols

    # ── 引用图构建 ────────────────────────────────────

    def _build_reference_graph(self, files: List[Path]):
        """为所有符号构建跨文件引用图。"""
        for file_path in files:
            try:
                content = file_path.read_text(encoding="utf-8")
            except Exception:
                continue

            fp = str(file_path)
            lang = _LANG_MAP.get(file_path.suffix.lower(), "unknown")

            # 对于每个已知符号，搜索引用
            known_names = self._get_project_symbol_names()
            for name in known_names:
                # 跳过太短的名字（噪声大）
                if len(name) < 2:
                    continue
                pattern = re.compile(rf'\b{re.escape(name)}\b')
                for m in pattern.finditer(content):
                    # 排除定义行
                    ref_line = content[:m.start()].count("\n") + 1
                    ctx_start = max(0, m.start() - 40)
                    ctx_end = min(len(content), m.end() + 40)
                    context = content[ctx_start:ctx_end].replace("\n", " ").strip()

                    # 检查是否是定义本身
                    is_def = any(
                        s.file == fp and s.line == ref_line
                        for s in self._symbols.get(name, [])
                    )
                    if is_def:
                        continue

                    ref = Reference(name, fp, ref_line, context)
                    self._references[name].append(ref)
                    self._stats["references"] += 1

    def _get_project_symbol_names(self) -> Set[str]:
        """获取项目中所有已索引的符号名。"""
        return {name for name, syms in self._symbols.items() if syms}

    # ── 查询 API ──────────────────────────────────────

    def find_symbols(self, query: str, kind: str = None,
                     limit: int = 20) -> List[dict]:
        """按名称搜索符号。支持模糊匹配。"""
        results = []
        query_lower = query.lower()

        for name, syms in self._symbols.items():
            if query_lower in name.lower():
                for s in syms:
                    if kind and s.kind != kind:
                        continue
                    results.append(s.to_dict())

        results.sort(key=lambda r: (0 if r["name"].lower() == query_lower else 1,
                                     r["kind"], r["name"]))
        return results[:limit]

    def find_references(self, symbol_name: str,
                        limit: int = 50) -> List[dict]:
        """查找符号的所有引用位置。"""
        refs = self._references.get(symbol_name, [])
        return [
            {"symbol": r.symbol_name, "file": r.file, "line": r.line, "context": r.context}
            for r in refs[:limit]
        ]

    def get_file_symbols(self, file_path: str) -> List[dict]:
        """获取文件中定义的所有符号。"""
        fp = str(Path(file_path).resolve())
        names = self._file_symbols.get(fp, [])
        results = []
        for name in names:
            for s in self._symbols.get(name, []):
                if s.file == fp:
                    results.append(s.to_dict())
        results.sort(key=lambda r: r["line"])
        return results

    def search_codebase(self, query: str, limit: int = 10) -> List[dict]:
        """全文搜索代码库（grep 级别，搜索文件内容）。"""
        results = []
        query_lower = query.lower()
        for file_path, symbol_names in self._file_symbols.items():
            if len(results) >= limit:
                break
            try:
                with open(file_path, encoding="utf-8") as f:
                    for i, line_content in enumerate(f, 1):
                        if query_lower in line_content.lower():
                            results.append({
                                "file": file_path,
                                "line": i,
                                "content": line_content.strip()[:200],
                            })
                            if len(results) >= limit:
                                break
            except Exception:
                continue
        return results

    def get_stats(self) -> dict:
        return {
            **self._stats,
            "indexed_files": len(self._indexed_files),
            "unique_symbols": len(self._symbols),
            "referenced_symbols": len(self._references),
            "index_time_ms": int(self._index_time * 1000),
        }

    def clear(self):
        """清除所有索引数据。"""
        self._symbols.clear()
        self._references.clear()
        self._file_symbols.clear()
        self._indexed_files.clear()
        self._file_mtimes.clear()
        self._stats = {"files": 0, "symbols": 0, "references": 0, "lines": 0}


# 模块级单例
_indexer: Optional[CodeIndexer] = None


def get_indexer(root_dir: str = None) -> CodeIndexer:
    global _indexer
    if _indexer is None or (root_dir and str(_indexer.root) != str(Path(root_dir).resolve())):
        _indexer = CodeIndexer(root_dir)
    return _indexer
