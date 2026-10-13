"""迁移的集成测试：保证 alembic 能从零构建出与模型一致的数据库。

这是"测试套件里最慢但最值钱"的一类用例：
它防止两种事故 ——
1. 有人改了 models.py 却忘了生成迁移（alembic check 会抓到）；
2. 迁移脚本本身写错了，新环境从零部署时建不出表（upgrade head 会抓到）。
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _run_alembic(args: list[str], db_path: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": f"sqlite+pysqlite:///{db_path.as_posix()}"}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.fixture()
def migrated_db(tmp_path):
    db_path = tmp_path / "migrated.db"
    result = _run_alembic(["upgrade", "head"], db_path)
    assert result.returncode == 0, f"迁移失败:\n{result.stdout}\n{result.stderr}"
    return db_path


def test_migrations_create_all_tables(migrated_db):
    engine = create_engine(f"sqlite+pysqlite:///{migrated_db.as_posix()}")
    tables = set(inspect(engine).get_table_names())
    assert {"users", "dramas", "episodes", "shots"} <= tables
    engine.dispose()


def test_migrations_match_models(migrated_db):
    """alembic check 必须报告零差异 —— 防止改模型忘了生成迁移。"""
    result = _run_alembic(["check"], migrated_db)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "No new upgrade operations detected" in output


def test_audio_prompt_column_exists(migrated_db):
    engine = create_engine(f"sqlite+pysqlite:///{migrated_db.as_posix()}")
    columns = {c["name"] for c in inspect(engine).get_columns("shots")}
    assert "audio_prompt" in columns
    engine.dispose()
