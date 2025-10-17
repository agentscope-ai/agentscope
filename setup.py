# -*- coding: utf-8 -*-
"""Setup script for AgentScope."""
import re
import setuptools

# obtain version from src/agentscope/_version.py
with open("src/agentscope/_version.py", encoding="UTF-8") as f:
    VERSION = re.search(
        r'^__version__\s*=\s*[\'"]([^\'"]*)[\'"]',
        f.read(),
        re.MULTILINE,
    ).group(1)

NAME = "agentscope"
URL = "https://github.com/agentscope-ai/agentscope"

minimal_requires = [
    "aioitertools",
    "anthropic",
    "dashscope",
    "docstring_parser",
    "json5",
    "json_repair",
    "mcp>=1.13",
    "numpy",
    "openai",
    "python-datauri",
    "opentelemetry-api",
    "opentelemetry-sdk",
    "opentelemetry-exporter-otlp",
    "python-socketio",
    "shortuuid",
    "tiktoken",
    "sounddevice",
]

extra_requires = [
    # Model
    "ollama",
    "google-genai",
    "Pillow",
    "transformers",
    "jinja2",
    # Evaluator
    "ray",
    # Long-term memory
    "mem0ai",
    "packaging",
    # RAG
    "pypdf",
    "nltk",
    "qdrant-client",
]

dev_requires = [
    "pre-commit",
    "pytest",
    "sphinx-gallery",
    "furo",
    "myst_parser",
    "matplotlib",
]

with open("README.md", "r", encoding="UTF-8") as fh:
    long_description = fh.read()

setuptools.setup(
    name=NAME,
    version=VERSION,
    author="SysML team of Alibaba Tongyi Lab ",
    author_email="gaodawei.gdw@alibaba-inc.com",
    description="AgentScope: A Flexible yet Robust Multi-Agent Platform.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url=URL,
    download_url=f"{URL}/archive/v{VERSION}.tar.gz",
    keywords=["deep-learning", "multi agents", "agents"],
    package_dir={"": "src"},
    packages=setuptools.find_packages("src"),
    install_requires=minimal_requires,
    extras_require={
        "full": minimal_requires + extra_requires,
        "dev": minimal_requires + extra_requires + dev_requires,
    },
    license="Apache-2.0",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.10",
    entry_points={},
)
