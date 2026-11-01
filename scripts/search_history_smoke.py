"""Check recent-search UI and persistence without contacting the search provider."""

import json
import re
from urllib.parse import parse_qs, urlsplit


def check_search_history(page, base):
    bootstrap = page.request.get(base + "/api/bootstrap").json()
    assert bootstrap["state"]["preview"], "Refuse a live device session"
    assert not bootstrap["library"].get("recent_searches"), "Refuse existing search history"
    lookups = []

    def respond(route):
        query = parse_qs(urlsplit(route.request.url).query)["q"][0]
        lookups.append(query)
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "places": [{"name": query, "lat": 0, "lon": 0, "address": "Preview result"}]}))

    pattern = re.compile(re.escape(base) + r"/api/search\?")
    page.route(pattern, respond)
    field = page.locator("#place-search")

    def search(query):
        field.fill(query)
        field.press("Enter")
        page.locator(".search-result").wait_for(state="visible")
        page.wait_for_function("!document.querySelector('#search-submit').disabled")

    def history():
        return page.request.get(base + "/api/library").json()["recent_searches"]

    try:
        for query in ("서울역", "東京大学", "서울역"):
            search(query)
        assert history() == ["서울역", "東京大学"]
        page.reload()
        page.wait_for_selector("#map-style")
        field.click()
        page.locator(".recent-search-open").first.wait_for(state="visible")
        assert page.locator(".recent-search-open").all_text_contents() == ["서울역", "東京大学"]
        field.fill("東京")
        assert page.locator(".recent-search-open").all_text_contents() == ["東京大学"]
        assert len(lookups) == 3, "Typing must not make search requests"
        field.fill("")
        page.locator(".recent-search-open").filter(has_text="서울역").click()
        page.locator(".search-result").wait_for(state="visible")
        page.wait_for_function("!document.querySelector('#search-submit').disabled")
        assert len(lookups) == 4 and history() == ["서울역", "東京大学"]
        field.fill("")
        field.press("ArrowDown")
        assert page.locator(".recent-search-open").first.evaluate("e=>e===document.activeElement")
        page.keyboard.press("Escape")
        assert not page.locator("#search-results").is_visible()
        field.click()
        page.locator(".recent-search-delete").first.click()
        page.wait_for_function("document.querySelectorAll('.recent-search-open').length===1")
        assert history() == ["東京大学"] and len(lookups) == 4
        page.locator("#clear-search-history").click()
        page.wait_for_function("document.querySelector('#search-results').hidden")
        assert history() == []
        page.reload()
        page.wait_for_selector("#map-style")
        field.click()
        assert not page.locator("#search-results").is_visible()
        state = page.request.get(base + "/api/state").json()
        assert state["position"] == bootstrap["state"]["position"]
        assert state["heartbeat"] == bootstrap["state"]["heartbeat"]
        return {"deduplicated": True, "persisted": True, "replay": True,
                "deleteAndClear": True, "localFilterOnly": True, "deviceUnchanged": True}
    finally:
        page.unroute(pattern, respond)
