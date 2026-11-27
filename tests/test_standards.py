from spec_agent.schemas.standard import StandardInput
from spec_agent.services.standards import demo_standard


def test_register_activate_and_snapshot(client, inspection_payload):
    payload = demo_standard().model_dump(mode="json", include=set(StandardInput.model_fields))
    payload["revision"] = "custom-1"
    response = client.post("/api/standards", json=payload)
    assert response.status_code == 201
    standard = response.json()
    data = {**inspection_payload, "standard_id": standard["id"]}
    assert client.post("/api/inspections", json=data).status_code == 409
    endpoint = f"/api/standards/{standard['id']}"
    response = client.post(
        endpoint + "/source",
        files={"file": ("example.pdf", b"%PDF-1.4\nfixture", "application/pdf")},
    )
    assert response.status_code == 200
    assert client.get(endpoint + "/source").content == b"%PDF-1.4\nfixture"
    assert client.post(endpoint + "/activate", json={}).status_code == 200
    state = client.post("/api/inspections", json=data).json()
    assert state["standard"]["revision"] == "custom-1"
    assert client.post(endpoint + "/activate", json={}).status_code == 409
    assert (
        client.post(
            endpoint + "/source", files={"file": ("test.pdf", b"%PDF-1.4\nother")}
        ).status_code
        == 409
    )


def test_invalid_and_missing_resources(client):
    assert client.get("/api/inspections/missing").status_code == 404
    assert client.post("/api/standards", json={}).status_code == 422
    assert client.get("/api/standards?customer=does-not-exist").json() == []
    assert client.get("/api/health").json()["external_ai"] is False
