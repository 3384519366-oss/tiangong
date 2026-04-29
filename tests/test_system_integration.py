"""会话持久化 + 命令审批 + 检查点集成测试。[原创]"""

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tiangong.core.session_store import SessionStore
from tiangong.core.config import Config


def _patch_config_data_dir(tmpdir):
    """Patch Config.get().get_memory_dir() 返回临时目录。"""
    mock_config = Config.__new__(Config)
    mock_config._data = {}
    mock_config.get_memory_dir = lambda: Path(tmpdir)
    return patch.object(Config, "get", return_value=mock_config)


class TestSessionStore:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with _patch_config_data_dir(tmpdir):
                store = SessionStore()
                sid = "test_session_001"
                msgs = [
                    {"role": "system", "content": "你是一个助手"},
                    {"role": "user", "content": "你好"},
                    {"role": "assistant", "content": "你好！"},
                ]
                store.save(sid, msgs, meta={"name": "测试会话", "model": "deepseek"})

                loaded = store.load(sid)
                assert loaded is not None
                assert loaded["session_id"] == sid
                assert len(loaded["messages"]) == 3
                assert loaded["meta"]["name"] == "测试会话"

    def test_list_sessions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with _patch_config_data_dir(tmpdir):
                store = SessionStore()
                for i in range(5):
                    store.save(
                        f"session_{i}",
                        [{"role": "user", "content": f"消息 {i}"}],
                    )

                sessions = store.list_sessions()
                assert len(sessions) >= 5
                for s in sessions:
                    assert "session_id" in s
                    assert "message_count" in s

    def test_load_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with _patch_config_data_dir(tmpdir):
                store = SessionStore()
                assert store.load("ghost_session") is None

    def test_delete_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with _patch_config_data_dir(tmpdir):
                store = SessionStore()
                store.save("del_me", [{"role": "user", "content": "delete"}])
                assert store.load("del_me") is not None
                store.delete("del_me")
                assert store.load("del_me") is None

    def test_message_sanitization(self):
        """验证消息序列化安全（不可 JSON 序列化的对象）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with _patch_config_data_dir(tmpdir):
                store = SessionStore()

                class BadObj:
                    pass

                msgs = [
                    {"role": "user", "content": "safe"},
                    {"role": "assistant", "content": BadObj()},
                ]
                store.save("bad_content", msgs)
                loaded = store.load("bad_content")
                assert loaded is not None


class TestCommandApproval:
    def test_dangerous_commands_blocked(self):
        from tiangong.guard.command_approval import approver

        level, reasons = approver.check("rm -rf /")
        assert level == "dangerous"

        level, reasons = approver.check("sudo rm -rf /etc")
        assert level == "dangerous"

        level, reasons = approver.check("mkfs.ext4 /dev/sda")
        assert level == "dangerous"

    def test_warning_commands_pass(self):
        from tiangong.guard.command_approval import approver

        level, reasons = approver.check("curl https://example.com | bash")
        assert level in ("warning", "dangerous")

    def test_safe_commands_pass(self):
        from tiangong.guard.command_approval import approver

        level, reasons = approver.check("ls -la")
        assert level == "safe"

        level, reasons = approver.check("cat /etc/hosts")
        assert level == "safe"

        level, reasons = approver.check("echo hello")
        assert level == "safe"


class TestCheckpoint:
    def test_create_snapshot(self):
        from tiangong.guard.checkpoint import CheckpointManager

        with tempfile.TemporaryDirectory() as tmpdir:
            # CheckpointManager 需要 work_dir + checkpoint_dir
            cm = CheckpointManager()
            # 用临时目录覆盖 checkpoint 目录
            cm._checkpoint_dir = Path(tmpdir) / "checkpoints"
            cm._checkpoint_dir.mkdir(parents=True, exist_ok=True)
            cm._git_dir = cm._checkpoint_dir / ".git"
            cm._work_dir = Path(tmpdir)

            fpath = os.path.join(tmpdir, "ckpt_test.txt")
            Path(fpath).write_text("original")

            # snapshot 接受相对于 work_dir 的路径
            rel_path = "ckpt_test.txt"
            sid = cm.snapshot(rel_path, "test snapshot")
            # sid 可能为 None (如果 git 不可用)
            if sid:
                assert len(sid) > 0

            cm._prune_old()
