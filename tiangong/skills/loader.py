"""技能引擎 — 渐进式加载（3层）[H+CC]

借鉴 Hermes: 渐进披露 3 层架构 + SKILL.md 格式
借鉴 Claude Code: 插件市场概念 + 技能命令系统

3 层渐进披露:
- 第1层(系统提示): 仅技能名 + 一句话描述
- 第2层(按需加载): Agent 调用 skill_view(name) 获取完整 SKILL.md
- 第3层(关联文件): 模板、脚本、资源按需读取
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from tiangong.core.config import Config

logger = logging.getLogger(__name__)


class Skill:
    """单个技能的定义."""

    __slots__ = ("name", "category", "description", "path", "content",
                 "requires_tools", "platforms", "metadata")

    def __init__(self, name: str, path: Path):
        self.name = name
        self.path = path
        self.category = path.parent.name
        self.description = ""
        self.content = ""
        self.requires_tools: List[str] = []
        self.platforms: List[str] = []
        self.metadata: dict = {}

        self._load()

    def _load(self):
        """解析 SKILL.md 的 YAML frontmatter + markdown 正文。[H]"""
        skill_md = self.path / "SKILL.md"
        if not skill_md.exists():
            logger.warning("技能 %s 缺少 SKILL.md", self.name)
            return

        raw = skill_md.read_text(encoding="utf-8")

        # 解析 YAML frontmatter
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) >= 3:
                try:
                    fm = yaml.safe_load(parts[1])
                    if isinstance(fm, dict):
                        self.metadata = fm
                        self.description = fm.get("description", "")
                        self.requires_tools = fm.get("requires_tools", [])
                        self.platforms = fm.get("platforms", [])
                except yaml.YAMLError:
                    pass
                self.content = parts[2].strip()
        else:
            self.content = raw.strip()

    @property
    def tier1_prompt(self) -> str:
        """第1层：仅名称 + 描述（注入系统提示）。[H]"""
        if self.description:
            return f"- **{self.name}**: {self.description}"
        return f"- **{self.name}**"

    @property
    def tier2_content(self) -> str:
        """第2层：完整 SKILL.md 内容。[H]"""
        return self.content

    def tier3_file(self, file_path: str) -> Optional[str]:
        """第3层：读取关联文件。[H]"""
        target = self.path / file_path
        if target.exists() and target.is_file():
            return target.read_text(encoding="utf-8")
        return None


class SkillRegistry:
    """技能注册表 — 管理所有已安装的技能。"""

    def __init__(self):
        self._skills: Dict[str, Skill] = {}
        self._loaded = False

    def load_all(self):
        """加载所有技能目录。"""
        if self._loaded:
            return

        config = Config.get()
        skills_dir = config.get_skills_dir()

        # 内置技能
        builtin_dir = Path(__file__).parent / "builtin"
        self._scan_directory(builtin_dir)

        # 用户技能
        if skills_dir.exists():
            self._scan_directory(skills_dir)

        self._loaded = True
        logger.info("技能引擎已加载: %d 个技能", len(self._skills))

    def _scan_directory(self, directory: Path):
        """扫描目录中的技能。[H]"""
        for skill_dir in directory.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            name = skill_dir.name
            try:
                self._skills[name] = Skill(name, skill_dir)
            except Exception as e:
                logger.warning("加载技能 %s 失败: %s", name, e)

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def list_all(self) -> List[Skill]:
        return list(self._skills.values())

    def get_tier1_prompt(self) -> str:
        """获取所有技能的第1层描述，用于注入系统提示。[H]"""
        if not self._skills:
            return ""

        lines = ["═══ 可用技能 ═══"]
        for skill in sorted(self._skills.values(), key=lambda s: s.name):
            lines.append(skill.tier1_prompt)
        return "\n".join(lines)

    def get_tier2(self, name: str) -> Optional[str]:
        """获取技能的第2层完整内容。[H]"""
        skill = self._skills.get(name)
        if skill:
            return skill.tier2_content
        return None

    def get_tier3(self, name: str, file_path: str) -> Optional[str]:
        """获取技能的第3层关联文件。[H]"""
        skill = self._skills.get(name)
        if skill:
            return skill.tier3_file(file_path)
        return None


# 模块级单例
skill_registry = SkillRegistry()


def init_skills():
    """初始化技能系统。"""
    skill_registry.load_all()


def get_skill_registry() -> SkillRegistry:
    return skill_registry
