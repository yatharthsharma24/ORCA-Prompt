import json

from fastapi.testclient import TestClient

from orca.api.app import create_app
from orca.llm import ScriptedLLM


class HealthyScripted(ScriptedLLM):
    async def health(self, model):
        return {"reachable": True, "model": model, "model_available": True}


def _replies():
    return [
        'Thought: plan\nFinal Answer: {"steps": [{"specialist": "context", "task": "define DHW"}]}',
        'Thought: search\nAction: search_knowledge\nAction Input: {"query": "degree heating weeks"}',
        "Thought: done\nFinal Answer: DHW Level 2 starts at 8 degC-weeks [E1].",
        "Severe bleaching is likely from 8 degC-weeks [E1].\n\n**Key findings**\n- Alert Level 2 begins at 8 degC-weeks [E1].",
    ]


def test_health_config_datasets(settings):
    with TestClient(create_app(settings, HealthyScripted([]))) as c:
        assert c.get("/api/health").json()["status"] == "ok"
        assert c.get("/api/ready").json()["ready"] is True
        assert len(c.get("/api/datasets").json()) == 5
        assert c.get("/api/datasets/D-OCEAN").json()["synthetic"] is True
        assert c.get("/api/datasets/nope").status_code == 404
        assert c.get("/api/config").json()["examples"]
        assert c.get("/").status_code == 200


def test_ask_json_and_run_history(settings):
    with TestClient(create_app(settings, HealthyScripted(_replies()))) as c:
        r = c.post("/api/ask?stream=false", json={"question": "What DHW means severe bleaching?"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["verification"]["passed"]
        assert body["references"][0]["sources"][0]["id"] == "K-DHW"
        run = c.get(f"/api/runs/{body['run_id']}").json()
        assert run["status"] == "done" and run["passed"] == 1
        assert c.get("/api/runs").json()[0]["id"] == body["run_id"]


def test_ask_streams_server_sent_events(settings):
    with TestClient(create_app(settings, HealthyScripted(_replies()))) as c:
        with c.stream("POST", "/api/ask", json={"question": "What DHW means severe bleaching?"}) as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            text = "".join(r.iter_text())
    events = [line[7:] for line in text.splitlines() if line.startswith("event: ")]
    assert events[0] == "accepted" and events[-1] == "final"
    assert {"plan", "step_started", "observation", "verification"} <= set(events)
    final = json.loads(text.split("event: final\ndata: ")[1].split("\n\n")[0])
    assert final["verification"]["passed"]


def test_api_key_and_validation(settings):
    settings.api_key = "s3cret"
    with TestClient(create_app(settings, HealthyScripted(_replies()))) as c:
        assert c.post("/api/ask?stream=false", json={"question": "hello there"}).status_code == 401
        assert c.post("/api/ask?stream=false", json={"question": "x"}, headers={"X-API-Key": "s3cret"}).status_code == 422
        ok = c.post("/api/ask?stream=false", json={"question": "What DHW means severe bleaching?"},
                    headers={"X-API-Key": "s3cret"})
        assert ok.status_code == 200


def test_llm_failure_is_503_and_recorded(settings):
    with TestClient(create_app(settings, HealthyScripted([]))) as c:
        r = c.post("/api/ask?stream=false", json={"question": "anything at all"})
        assert r.status_code == 503
        assert c.get("/api/runs").json()[0]["status"] == "error"


def test_ingest_registers_real_dataset(settings, tmp_path):
    import shutil

    from orca.data.ingest import ingest
    from orca.data.store import MarineStore

    db = tmp_path / "copy.sqlite"
    shutil.copy(settings.data_db, db)
    csv = tmp_path / "sst.csv"
    csv.write_text("Year,Region,Mean SST\n2020,Kerala,29.1\n2021,Kerala,28.7\n", encoding="utf-8")
    n = ingest(csv, db, "real_sst", "D-REAL", "Real SST", "Someone (2025). SST.", url="https://example.org")
    assert n == 2
    store = MarineStore(db)
    ds = store.dataset("D-REAL")
    assert not ds.synthetic and list(ds.columns) == ["year", "region", "mean_sst"]
    assert store.query("SELECT mean_sst FROM real_sst WHERE year=2021")[0]["mean_sst"] == 28.7
