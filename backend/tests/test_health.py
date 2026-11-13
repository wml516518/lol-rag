"""/api/health 契约测试：字段名一个都不能错。"""
from __future__ import annotations

EXPECTED_KEYS = {"status", "data_version", "champions", "items", "model"}


def test_health_ok(client):
    response = client.get("/api/health")
    assert response.status_code == 200

    body = response.json()
    assert set(body) == EXPECTED_KEYS
    assert body["status"] == "ok"
    assert isinstance(body["data_version"], str) and body["data_version"]
    assert isinstance(body["champions"], int) and body["champions"] > 0
    assert isinstance(body["items"], int) and body["items"] > 0
    assert body["model"] == "deepseek-chat"


def test_health_cors_header(client):
    response = client.get(
        "/api/health", headers={"Origin": "http://localhost:5173"}
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "*"

def test_startup_fails_with_chinese_hint_when_db_missing(monkeypatch):
    """契约：data/lol.db 不存在时启动失败并给明确中文提示，不静默降级。"""
    from fastapi.testclient import TestClient

    from app import main

    # db_bridge 在 import 时就绑定了 settings.db_path，直接改 env 最可靠
    monkeypatch.setenv("LOL_DB_PATH", "/tmp/lol-rag-no-such.db")
    try:
        with TestClient(main.app):
            raise AssertionError("数据库缺失时不应启动成功")
    except RuntimeError as exc:
        assert "未找到数据库" in str(exc)
        assert "fetch_data.py" in str(exc)
