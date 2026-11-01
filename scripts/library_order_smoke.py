"""Exercise real library drag/drop in an isolated preview session."""


def check_library_order(page, base):
    bootstrap = page.request.get(base + "/api/bootstrap").json()
    assert bootstrap["state"]["preview"], "Refuse a live device session"
    assert not bootstrap["library"]["routes"] and not bootstrap["library"]["places"], "Refuse a nonempty library"
    headers = {"X-Bloom-Token": bootstrap["token"]}
    original_state = bootstrap["state"]
    fixtures = {}
    for kind in ("routes", "places"):
        ids = {}
        for name in reversed(("Visible A", "Hidden B", "Visible C", "Hidden D")):
            value = {"name": name, "lat": 0, "lon": 0}
            if kind == "routes":
                value = {"name": name, "points": [{"lat": 0, "lon": 0}, {"lat": 0, "lon": .001}]}
            response = page.request.post(base + "/api/library/save", headers=headers,
                                         data={"kind": kind, "value": value})
            assert response.ok, response.text()
            ids[name] = response.json()["id"]
        fixtures[kind] = ids
    page.reload()
    page.wait_for_selector("#map-style")

    def wait_order(host, expected):
        page.wait_for_function(
            "([host,expected]) => JSON.stringify(Array.from(document.querySelectorAll(host+' .library-row'),row=>row.dataset.id)) === JSON.stringify(expected)",
            arg=[host, expected])

    for kind in ("routes", "places"):
        ids = fixtures[kind]
        a, b, c, d = [ids[name] for name in ("Visible A", "Hidden B", "Visible C", "Hidden D")]
        host = "#saved-routes" if kind == "routes" else "#saved-places"
        page.locator(f'[data-tab="{kind}"]').click()
        wait_order(host, [a, b, c, d])
        source = page.locator(f'{host} [data-id="{a}"] .library-grip')
        target = page.locator(f'{host} [data-id="{c}"]')
        source.drag_to(target, target_position={"x": 70, "y": target.bounding_box()["height"] - 4})
        wait_order(host, [b, c, a, d])
        assert not page.locator("#editor-dialog").is_visible()
        assert page.request.get(base + "/api/library").json()[kind][2]["id"] == a

        page.wait_for_timeout(300)
        page.locator(f'{host} [data-id="{a}"] .rename-item').click()
        page.locator("#dialog-fields input").first.fill("Visible A renamed")
        page.locator("#confirm-dialog").click()
        page.wait_for_function(
            "([host,id])=>document.querySelector(host+' [data-id=\"'+id+'\"] strong')?.textContent==='Visible A renamed'",
            arg=[host, a])
        wait_order(host, [b, c, a, d])

        filter_input = page.locator("#route-filter" if kind == "routes" else "#place-filter")
        filter_input.fill("Visible")
        wait_order(host, [c, a])
        source = page.locator(f'{host} [data-id="{a}"] .library-grip')
        source.drag_to(page.locator(f'{host} [data-id="{c}"]'), target_position={"x": 70, "y": 4})
        wait_order(host, [a, c])
        filter_input.fill("")
        wait_order(host, [b, a, c, d])
        page.locator(f'{host} [data-id="{a}"] .library-grip').press("ArrowDown")
        wait_order(host, [b, c, a, d])
        page.reload()
        page.wait_for_selector("#map-style")
        page.locator(f'[data-tab="{kind}"]').click()
        wait_order(host, [b, c, a, d])
    final_state = page.request.get(base + "/api/state").json()
    assert final_state["position"] == original_state["position"]
    assert final_state["heartbeat"] == original_state["heartbeat"]
    return {"routes": True, "places": True, "filteredDrag": True, "renameKeepsOrder": True,
            "reloadPersists": True, "keyboardReorder": True, "deviceUnchanged": True}
