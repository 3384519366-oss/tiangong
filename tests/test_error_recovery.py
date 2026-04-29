"""测试自动错误恢复: 错误模式匹配 + 重试控制。[原创]"""

import json
import pytest
from tiangong.guard.error_recovery import (
    analyze_error,
    ErrorReport,
    RetryController,
    _adjust_args,
    _ERROR_PATTERNS,
)


class TestErrorPatterns:
    def test_command_not_found(self):
        r = analyze_error("zsh: python: command not found")
        assert r.category == "command_not_found"
        assert r.auto_fixable is False

    def test_file_not_found(self):
        r = analyze_error("No such file or directory: /tmp/nonexistent.txt")
        assert r.category == "file_not_found"

    def test_permission_denied(self):
        r = analyze_error("Permission denied")
        assert r.category == "permission_denied"

    def test_module_not_found(self):
        r = analyze_error("ModuleNotFoundError: No module named 'numpy'")
        assert r.category == "import_error"
        assert "pip install" in r.message.lower()

    def test_syntax_error(self):
        r = analyze_error("SyntaxError: invalid syntax")
        assert r.category == "syntax_error"

    def test_connection_refused(self):
        r = analyze_error("Connection refused")
        assert r.category == "network_error"
        assert r.auto_fixable is True

    def test_connection_timeout(self):
        r = analyze_error("Connection timed out")
        assert r.category == "network_error"

    def test_timeout(self):
        r = analyze_error("Command timed out after 120 seconds")
        assert r.category == "timeout"
        assert r.auto_fixable is True

    def test_git_error(self):
        r = analyze_error("fatal: not a git repository")
        assert r.category == "git_error"

    def test_brew_error(self):
        r = analyze_error("No formulae found")
        assert r.category == "brew_error"

    def test_exit_code(self):
        r = analyze_error("exit code: 1")
        assert r.category == "exit_code"

    def test_unknown_error(self):
        r = analyze_error("完全无法识别的随机错误信息")
        assert r.category == "unknown"

    def test_json_error_extraction(self):
        """从 JSON 包装中提取错误。"""
        r = analyze_error(json.dumps({"error": "Permission denied"}))
        assert r.category == "permission_denied"


class TestRetryController:
    def test_initial_state(self):
        rc = RetryController()
        assert rc.should_retry("bash") is True
        assert rc.get_retry_count("bash") == 0

    def test_max_retries(self):
        rc = RetryController(max_retries=3)
        for i in range(3):
            assert rc.should_retry("bash") is True
            rc.record_attempt("bash", {}, f"error {i}",
                            ErrorReport())
        assert rc.should_retry("bash") is False
        assert rc.get_retry_count("bash") == 3

    def test_delay_backoff(self):
        rc = RetryController()
        rc.record_attempt("bash", {}, "error",
                        ErrorReport())
        delay1 = rc.delay("bash")
        rc.record_attempt("bash", {}, "error2",
                        ErrorReport())
        delay2 = rc.delay("bash")
        # 第2次退避更大
        assert delay2 >= delay1

    def test_context_for_llm(self):
        rc = RetryController()
        report = ErrorReport()
        report.message = "命令未找到"
        rc.record_attempt("bash", {}, "错误", report)
        ctx = rc.get_context_for_llm()
        assert "命令未找到" in ctx
        assert "bash" in ctx

    def test_reset(self):
        rc = RetryController()
        rc.record_attempt("bash", {}, "e", ErrorReport())
        rc.reset()
        assert rc.get_retry_count("bash") == 0
        assert rc.get_context_for_llm() == ""


class TestArgAdjustment:
    def test_timeout_adjustment(self):
        report = ErrorReport()
        report.category = "timeout"
        adjusted = _adjust_args("bash", {"timeout": 120, "command": "ls"}, report)
        assert adjusted["timeout"] == 240  # doubled

    def test_timeout_cap_at_600(self):
        report = ErrorReport()
        report.category = "timeout"
        adjusted = _adjust_args("bash", {"timeout": 400, "command": "ls"}, report)
        assert adjusted["timeout"] == 600  # capped

    def test_network_timeout_adjustment(self):
        report = ErrorReport()
        report.category = "network_error"
        adjusted = _adjust_args("bash", {"timeout": 60, "command": "curl"}, report)
        assert adjusted["timeout"] == 120  # +60
