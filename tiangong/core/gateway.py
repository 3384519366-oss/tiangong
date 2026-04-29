"""天工 AI 助手 Gateway — CLI 入口."""

import logging
import sys

from tiangong.core.config import Config, ConfigError
from tiangong.core.agent import TianGongAgent


def setup_logging():
    config = Config.get()
    level = config.data.get("logging", {}).get("level", "WARNING")
    logging.basicConfig(
        level=getattr(logging, level, logging.WARNING),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main():
    # 安装向导
    if "--setup" in sys.argv or "-s" in sys.argv:
        from tiangong.core.wizard import run_wizard
        run_wizard()
        return

    # 首次运行自动检测
    from tiangong.core.wizard import needs_setup
    if needs_setup():
        print("天工 — 首次运行，进入配置向导...\n")
        from tiangong.core.wizard import run_wizard
        run_wizard()
        print()
        input("按 Enter 键启动天工...")
        print()

    setup_logging()
    model_key = sys.argv[1] if len(sys.argv) > 1 else None

    try:
        agent = TianGongAgent(model_key=model_key)
    except ConfigError as e:
        print(f"配置错误: {e}")
        print("请运行 'tiangong --setup' 完成首次配置。")
        sys.exit(1)

    agent.run_cli()


if __name__ == "__main__":
    main()
