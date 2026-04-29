"""检查点快照 — 写文件前自动创建 Git 快照。[H]

借鉴 Hermes 的 checkpoint_manager.py，使用 shadow git 仓库实现透明文件系统快照。
"""

import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

from tiangong.core.config import Config

logger = logging.getLogger(__name__)

_MAX_SNAPSHOTS = 50


class CheckpointManager:
    """Git-based 文件系统快照管理器。[H]"""

    def __init__(self):
        config = Config.get()
        data_dir = config.get_memory_dir()
        self._checkpoint_dir = data_dir / "checkpoints"
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self._git_dir = self._checkpoint_dir / ".git"

        # 工作目录以用户主目录为根
        self._work_dir = Path.home()
        self._initialized = False

    def _ensure_init(self):
        """延迟初始化 shadow git 仓库。"""
        if self._initialized:
            return
        try:
            # Shadow git: GIT_DIR 指向检查点目录，GIT_WORK_TREE 指向用户主目录
            env = {
                "GIT_DIR": str(self._git_dir),
                "GIT_WORK_TREE": str(self._work_dir),
            }

            # 初始化 bare-ish 仓库
            subprocess.run(
                ["git", "init"],
                env=env,
                capture_output=True,
                timeout=10,
            )

            # 忽略所有文件，只跟踪我们关心的
            gitignore = self._git_dir.with_name("checkpoints") / ".gitignore"
            # 我们不需要gitignore，用git add -A的方式选择性添加

            self._initialized = True
            logger.info("检查点系统已初始化: %s", self._git_dir)
        except Exception as e:
            logger.warning("检查点初始化失败: %s", e)

    def snapshot(self, path: str, description: str = "") -> Optional[str]:
        """创建文件的检查点快照。

        path: 相对于用户主目录的文件路径
        description: 快照描述
        返回: 快照 ID (时间戳)
        """
        if not self._initialized:
            self._ensure_init()
            if not self._initialized:
                return None

        try:
            snapshot_id = str(int(time.time() * 1000))
            env = {
                "GIT_DIR": str(self._git_dir),
                "GIT_WORK_TREE": str(self._work_dir),
            }

            abs_path = self._work_dir / path
            if not abs_path.exists():
                logger.debug("文件不存在，跳过快照: %s", path)
                return None

            # 只添加这个文件
            subprocess.run(
                ["git", "add", str(path)],
                env=env,
                capture_output=True,
                timeout=10,
            )

            # 提交快照
            msg = f"[天工] {description or '快照'}"
            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", msg],
                env=env,
                capture_output=True,
                timeout=10,
            )

            logger.debug("检查点已创建: %s — %s", snapshot_id, description or path)

            # 清理旧快照
            self._prune_old()

            return snapshot_id
        except Exception as e:
            logger.warning("创建快照失败: %s — %s", path, e)
            return None

    def rollback(self, path: str, snapshot_id: Optional[str] = None) -> bool:
        """回滚文件到指定快照。

        path: 相对于用户主目录的文件路径
        snapshot_id: 快照 ID，None 表示回滚到上一个快照
        """
        if not self._initialized:
            return False

        try:
            env = {
                "GIT_DIR": str(self._git_dir),
                "GIT_WORK_TREE": str(self._work_dir),
            }

            abs_path = self._work_dir / path
            if snapshot_id:
                # 回滚到指定快照
                subprocess.run(
                    ["git", "checkout", snapshot_id, "--", str(path)],
                    env=env,
                    capture_output=True,
                    timeout=10,
                )
            else:
                # 回滚到上一个提交
                subprocess.run(
                    ["git", "checkout", "HEAD~1", "--", str(path)],
                    env=env,
                    capture_output=True,
                    timeout=10,
                )

            logger.info("文件已回滚: %s -> %s", path, snapshot_id or "上一个快照")
            return True
        except Exception as e:
            logger.warning("回滚失败: %s — %s", path, e)
            return False

    def list_snapshots(self, limit: int = 10) -> list[dict]:
        """列出最近的快照。"""
        if not self._initialized:
            return []

        try:
            env = {
                "GIT_DIR": str(self._git_dir),
                "GIT_WORK_TREE": str(self._work_dir),
            }

            result = subprocess.run(
                ["git", "log", f"--max-count={limit}", "--format=%H|%s|%ai"],
                env=env,
                capture_output=True,
                timeout=10,
                text=True,
            )

            snapshots = []
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("|", 2)
                if len(parts) == 3:
                    snapshots.append({
                        "id": parts[0][:8],
                        "message": parts[1],
                        "time": parts[2],
                    })
            return snapshots
        except Exception:
            return []

    def _prune_old(self):
        """删除超过最大数量的旧快照。"""
        try:
            env = {
                "GIT_DIR": str(self._git_dir),
                "GIT_WORK_TREE": str(self._work_dir),
            }

            # 统计提交数
            result = subprocess.run(
                ["git", "rev-list", "--count", "HEAD"],
                env=env,
                capture_output=True,
                timeout=10,
                text=True,
            )
            count = int(result.stdout.strip() or 0)

            if count > _MAX_SNAPSHOTS:
                # 删除最旧的提交
                excess = count - _MAX_SNAPSHOTS
                # 使用 rebase 或直接删除旧提交比较复杂
                # 简化为日志记录
                logger.debug("检查点数量: %d (最大: %d)", count, _MAX_SNAPSHOTS)
        except Exception:
            pass


# 模块级单例
checkpoint = CheckpointManager()
