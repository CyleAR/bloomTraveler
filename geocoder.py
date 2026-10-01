"""On-demand multilingual OSM search through Photon."""

from collections import OrderedDict
import json
import os
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from routes import RoutePoint


class Geocoder:
    def __init__(self):
        self.endpoint = os.environ.get("BLOOM_GEOCODER_URL", "https://photon.komoot.io/api/")
        self.lock = threading.Lock()
        self.cache = OrderedDict()
        self.last_request = 0

    def search(self, query):
        query = str(query).strip()
        if not query or len(query) > 200:
            raise ValueError("검색어는 1~200자로 입력하세요")
        with self.lock:
            if query in self.cache:
                self.cache.move_to_end(query)
                return self.cache[query]
            time.sleep(max(0, 1 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            req = Request(self.endpoint + "?" + urlencode({"q": query, "limit": 20}),
                          headers={"User-Agent": "BloomTraveler/2.0 (https://github.com/CyleAR/bloomTraveler)",
                                   "Accept": "application/json"})
            with urlopen(req, timeout=12) as response:
                data = json.load(response)
            features = data.get("features", [])
            university = any(word in query.lower() for word in ("大学", "대학교", "university"))
            def relevance(feature):
                props = feature.get("properties", {})
                name = str(props.get("name", ""))
                if name.casefold() == query.casefold():
                    return (0, 0)
                if university and props.get("osm_key") == "amenity" and props.get("osm_value") == "university":
                    return (1, len(name))
                return (2, 0)
            features.sort(key=relevance)
            places = []
            for feature in features[:8]:
                longitude, latitude = feature["geometry"]["coordinates"][:2]
                properties = feature.get("properties", {})
                point = RoutePoint.load({"lat": latitude, "lon": longitude,
                                        "name": properties.get("name") or properties.get("street") or query})
                address = " · ".join(dict.fromkeys(str(properties[key]) for key in
                                                ("city", "district", "state", "country") if properties.get(key)))
                places.append({"lat": point.lat, "lon": point.lon, "name": point.name, "address": address})
            self.cache[query] = places
            while len(self.cache) > 100:
                self.cache.popitem(last=False)
            return places
