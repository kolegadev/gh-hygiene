"""Setup fallback for older pip versions."""
from setuptools import setup, find_packages

setup(
    name="gh-hygiene",
    version="0.1.0",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "typer>=0.12",
        "PyGithub>=2.3",
        "rich>=13.7",
        "keyring>=25.0",
        "openai>=1.30",
        "pyyaml>=6.0",
        "fastapi>=0.109",
        "uvicorn>=0.27",
    ],
    extras_require={
        "dev": [
            "pytest>=8.0",
            "pytest-mock>=3.12",
        ],
    },
    entry_points={
        "console_scripts": [
            "gh-hygiene=gh_hygiene.cli:main",
        ],
    },
    python_requires=">=3.9",
)
