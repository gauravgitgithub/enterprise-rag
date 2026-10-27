import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# Break caught: migrations requiring the app role's DB URL, so a migration Job needs a secret
# it never uses. Runs outside the repo so no local .env can supply the missing value.
def test_migrations_need_only_the_admin_url(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("RAG_")}
    env["RAG_DATABASE_ADMIN_URL"] = "postgresql+asyncpg://u:p@localhost:5432/rag"

    result = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(ROOT),
            "alembic",
            "-c",
            str(ROOT / "alembic.ini"),
            "upgrade",
            "head",
            "--sql",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr[-500:]
    assert "CREATE POLICY tenant_isolation" in result.stdout
