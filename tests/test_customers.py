async def test_list_customers_shape(client) -> None:
    await client.post(
        "/api/v1/customers/bulk",
        json={
            "customers": [
                {"external_id": "lc1", "name": "Asha Rao", "city": "Pune"},
                {"external_id": "lc2", "name": "Bina Shah", "city": "Pune"},
            ]
        },
    )
    resp = await client.get("/api/v1/customers", params={"page": 0, "page_size": 20})
    assert resp.status_code == 200
    body = resp.json()
    assert {"data", "total", "page", "pageSize", "pageCount"}.issubset(body.keys())
    assert body["total"] >= 2
    assert isinstance(body["data"], list)
    # total_spend must serialize as a JS number, never a string
    assert all(isinstance(c["total_spend"], (int, float)) for c in body["data"])


async def test_upload_sets_spend(client) -> None:
    resp = await client.post(
        "/api/v1/customers/upload",
        json={
            "rows": [
                {
                    "external_id": "up1",
                    "name": "Spend One",
                    "total_spend": "12345.00",
                    "order_count": 4,
                }
            ]
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] + body["updated"] == 1

    listed = (
        await client.get("/api/v1/customers", params={"search": "Spend One"})
    ).json()
    match = [c for c in listed["data"] if c["external_id"] == "up1"]
    assert match
    assert match[0]["total_spend"] == 12345.0
    assert match[0]["order_count"] == 4


async def test_upload_upserts_on_conflict(client) -> None:
    await client.post(
        "/api/v1/customers/upload",
        json={"rows": [{"external_id": "u2", "name": "N", "total_spend": "10", "order_count": 1}]},
    )
    resp = await client.post(
        "/api/v1/customers/upload",
        json={"rows": [{"external_id": "u2", "name": "N2", "total_spend": "20", "order_count": 2}]},
    )
    body = resp.json()
    assert body["created"] == 0
    assert body["updated"] == 1
