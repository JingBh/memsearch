"""Regression tests for platform-specific Milvus dependency floors."""

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib


def test_milvus_dependency_floors_are_split_by_platform():
    pyproject = Path(__file__).parents[1] / "pyproject.toml"
    dependencies = tomllib.loads(pyproject.read_text())["project"]["dependencies"]
    milvus_dependencies = {
        requirement for requirement in dependencies if requirement.startswith(("milvus-lite", "pymilvus"))
    }

    assert milvus_dependencies == {
        "milvus-lite>=2.5.0; sys_platform != 'win32'",
        "milvus-lite>=3.1.1; sys_platform == 'win32'",
        "pymilvus>=2.5.0,!=2.6.10; sys_platform != 'win32'",
        "pymilvus>=2.6.11; sys_platform == 'win32'",
    }
