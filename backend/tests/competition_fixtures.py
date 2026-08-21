from collections.abc import Iterator
from pathlib import Path
import shutil
import uuid

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def repository_tmp_path() -> Iterator[Path]:
    """为必须位于 Git 忽略路径内的安全门测试提供隔离目录。"""

    path = REPOSITORY_ROOT / "output/pytest-repository-tmp" / uuid.uuid4().hex
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
