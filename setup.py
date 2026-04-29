"""Setup script for tiangong package."""

from setuptools import setup, find_packages

setup(
    name="tiangong",
    version="0.1.0",
    packages=find_packages(),
    python_requires=">=3.11",
    install_requires=[
        "openai>=2.0.0",
        "pyyaml>=6.0",
        "chromadb>=1.0.0",
        "edge-tts>=6.0",
        "rumps>=0.4.0",
        "rich>=13.0",
        # P0 fix: missing runtime dependencies
        "requests>=2.31.0",
        "Pillow>=10.0.0",
        "PyPDF2>=3.0.0",
        "pyobjc-framework-Quartz>=10.0; platform_system=='Darwin'",
    ],
    extras_require={
        "dev": [
            "pytest>=8.0.0",
            "pytest-asyncio>=0.23.0",
            "mypy>=1.8.0",
            "ruff>=0.2.0",
        ],
        "voice": [
            "faster-whisper>=1.0.0",
        ],
        "mcp": [
            "mcp>=1.0.0",
        ],
        "browser": [
            "playwright>=1.40.0",
        ],
        "feishu": [
            "lark-oapi>=1.0.0",
        ],
        "all": [
            "pytest>=8.0.0",
            "pytest-asyncio>=0.23.0",
            "mypy>=1.8.0",
            "ruff>=0.2.0",
            "faster-whisper>=1.0.0",
            "mcp>=1.0.0",
            "playwright>=1.40.0",
            "lark-oapi>=1.0.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "tiangong=tiangong.core.gateway:main",
            "tiangong-desktop=tiangong.platforms.desktop:main",
        ],
    },
)
