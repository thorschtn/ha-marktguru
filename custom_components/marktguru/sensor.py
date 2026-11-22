import asyncio
import json
import logging
import re
from datetime import timedelta, datetime, timezone
import aiohttp

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    CoordinatorEntity,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util
from .const import DOMAIN, CONF_PRODUCTS, CONF_ZIP, CONF_RETAILERS, API_URL, HEADERS, SEARCH_ENTITY

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(hours=1)

# Zweite Quelle: meinprospekt.de (deckt u. a. Marktkauf ab, das bei Marktguru fehlt)
MP_URL = "https://www.meinprospekt.de/search"
MP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "text/html",
    "Accept-Language": "de-DE,de;q=0.9",
}
MP_INTERVAL = timedelta(hours=6)
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _first_word(name):
    return ((name or "").split() or [""])[0].lower()


def _base_unit(short_name):
    """Einheit, auf die sich der Grundpreis bezieht (g -> kg, ml -> l)."""
    unit = (short_name or "").strip()
    if unit.lower() in ("g", "kg"):
        return "kg"
    if unit.lower() in ("ml", "l"):
        return "l"
    return unit or None


def _parse_base_price(text):
    """Aus '(1 kg = € 5.96)' wird (5.96, 'kg')."""
    match = re.search(r"([\d.,]*\s*[A-Za-zäöüÄÖÜ]+)\.?\s*=\s*(?:€\s*)?(\d+(?:[.,]\d+)?)", text or "")
    if not match:
        return None, None
    unit = re.sub(r"^1\s+", "", match.group(1).strip())
    try:
        return round(float(match.group(2).replace(",", ".")), 2), unit
    except ValueError:
        return None, None


def _first_url(node):
    if isinstance(node, str):
        return node if node.startswith("http") else None
    if isinstance(node, dict):
        node = list(node.values())
    if isinstance(node, list):
        for item in node:
            url = _first_url(item)
            if url:
                return url
    return None


def _local_iso(value):
    if not value:
        return None
    try:
        return dt_util.as_local(datetime.fromisoformat(value.replace("Z", "+00:00"))).isoformat()
    except Exception:
        return value


def _price(value):
    return round(value, 2) if isinstance(value, (int, float)) else value


def _normalize_mp(item, validity=None):
    """meinprospekt-Angebot in das Marktguru-Format bringen."""
    validity = validity or (None, None)
    prices = item.get("prices") or {}
    brand = item.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")
    base_price, base_unit = _parse_base_price(prices.get("priceByBaseUnit"))
    # Link auf die Prospektseite, auf der das Angebot steht
    parent = item.get("parentContent") or {}
    link = None
    if parent.get("id") and parent.get("type") == "static_brochure":
        link = f"https://www.meinprospekt.de/contentViewer/static/{parent['id']}"
        page = (parent.get("page") or {}).get("number")
        if page:
            link += f"?page={page}"
    return {
        "id": None,
        "price": _price(prices.get("mainPrice")),
        "oldPrice": _price(prices.get("secondaryPrice")),
        "referencePrice": base_price,
        "unit": {"shortName": base_unit} if base_unit else None,
        "description": item.get("description"),
        "brand": {"name": brand} if brand else None,
        "product": {"name": item.get("title")},
        "advertisers": [{"name": item.get("publisherName")}],
        "validityDates": [
            {
                "from": _local_iso(item.get("validFrom") or validity[0]),
                "to": _local_iso(item.get("validUntil") or validity[1]),
            }
        ],
        "_source": "meinprospekt",
        "_link": link,
        "_image_url": _first_url(item.get("offerImages")),
    }

async def async_setup_entry(hass, entry, async_add_entities):
    # Optionen (nachträglich geändert) haben Vorrang vor den Daten der Ersteinrichtung
    conf = {**entry.data, **entry.options}
    zip_code = conf[CONF_ZIP]
    products = conf[CONF_PRODUCTS]
    retailers = [
        r.strip().lower()
        for r in (conf.get(CONF_RETAILERS) or "").split(",")
        if r.strip()
    ]

    coordinator = MarktguruDataCoordinator(hass, zip_code, products, retailers)
    await coordinator.async_config_entry_first_refresh()

    current_sensors = {}

    def get_sensor_id(query, idx):
        return f"marktguru_{zip_code}_{query.replace(' ', '_').lower()}_{idx}"

    # Initiales Setup: Angebotssensoren
    sensors = []
    for product in products:
        name = product["name"]
        query = product["query"]
        offers = coordinator.data.get(query, [])
        for idx, offer in enumerate(offers):
            sensor_id = get_sensor_id(query, idx)
            if sensor_id not in current_sensors:
                sensor = MarktguruSensor(coordinator, name, query, idx, zip_code)
                sensors.append(sensor)
                current_sensors[sensor_id] = sensor

    # Queries-Sensor als CoordinatorEntity (wird immer behalten)
    queries_sensor_id = f"marktguru_queries_{zip_code}"
    queries_sensor = MarktguruQueriesSensor(coordinator, products, zip_code)
    sensors.append(queries_sensor)
    current_sensors[queries_sensor_id] = queries_sensor

    # Such-Sensor: liefert Treffer für den Text im Helfer SEARCH_ENTITY
    search_sensor_id = f"marktguru_search_{zip_code}"
    sensors.append(MarktguruSearchSensor(coordinator, zip_code))
    current_sensors[search_sensor_id] = sensors[-1]

    async_add_entities(sensors, True)

    # Verwaiste Sensoren entfernen (z. B. von gelöschten Suchbegriffen)
    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.unique_id not in current_sensors:
            registry.async_remove(reg_entry.entity_id)

    @callback
    def async_new_data():
        new_entities = []
        new_sensor_ids = set()

        # Angebote aus API
        for product in coordinator.products:
            name = product["name"]
            query = product["query"]
            offers = coordinator.data.get(query, [])
            for idx, offer in enumerate(offers):
                sensor_id = get_sensor_id(query, idx)
                new_sensor_ids.add(sensor_id)
                if sensor_id not in current_sensors:
                    sensor = MarktguruSensor(coordinator, name, query, idx, zip_code)
                    new_entities.append(sensor)
                    current_sensors[sensor_id] = sensor

        # Queries-Sensor immer behalten/ggf. neu anlegen
        queries_sensor_id = f"marktguru_queries_{zip_code}"
        new_sensor_ids.add(queries_sensor_id)
        new_sensor_ids.add(search_sensor_id)
        if queries_sensor_id not in current_sensors:
            queries_sensor = MarktguruQueriesSensor(coordinator, coordinator.products, zip_code)
            new_entities.append(queries_sensor)
            current_sensors[queries_sensor_id] = queries_sensor

        # Entferne Sensoren, die nicht mehr gebraucht werden
        sensors_to_remove = []
        for sensor_id in list(current_sensors.keys()):
            if sensor_id not in new_sensor_ids:
                sensors_to_remove.append(sensor_id)

        if new_entities:
            async_add_entities(new_entities)

        if sensors_to_remove:
            for sensor_id in sensors_to_remove:
                entity_id = registry.async_get_entity_id("sensor", DOMAIN, sensor_id)
                if entity_id:
                    registry.async_remove(entity_id)
                current_sensors.pop(sensor_id, None)

    entry.async_on_unload(coordinator.async_add_listener(async_new_data))

def _offer_summary(offer):
    """Kompakte Darstellung eines Angebots für die Suche."""
    offer_id = offer.get("id")
    dates = (offer.get("validityDates") or [{}])[0]
    return {
        "name": (offer.get("product") or {}).get("name"),
        "brand": (offer.get("brand") or {}).get("name"),
        "retailer": ((offer.get("advertisers") or [{}])[0]).get("name"),
        "price": offer.get("price"),
        "old_price": offer.get("oldPrice"),
        "grundpreis": offer.get("referencePrice"),
        "grundpreis_einheit": _base_unit((offer.get("unit") or {}).get("shortName")),
        "description": offer.get("description"),
        "valid_from": dates.get("from"),
        "valid_to": dates.get("to"),
        "image": offer.get("_image_url")
        or (f"https://mg2de.b-cdn.net/api/v1/offers/{offer_id}/images/default/0/small.webp" if offer_id else None),
        "link": offer.get("_link")
        or (f"https://www.marktguru.de/offers/{offer_id}" if offer_id else None),
        "source": offer.get("_source", "marktguru"),
    }


class MarktguruDataCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, zip_code, products, retailers=None):
        super().__init__(
            hass,
            _LOGGER,
            name=f"Marktguru Coordinator {zip_code}",
            update_interval=SCAN_INTERVAL,
        )
        self.zip_code = zip_code
        self.products = products
        self.retailers = retailers or []
        self._mp_cache = {}
        self._mp_time = None

    async def _fetch_meinprospekt(self, session, query):
        params = {
            "query": query,
            "lat": self.hass.config.latitude,
            "lng": self.hass.config.longitude,
        }
        async with session.get(
            MP_URL, headers=MP_HEADERS, params=params, timeout=aiohttp.ClientTimeout(total=20)
        ) as resp:
            resp.raise_for_status()
            html = await resp.text()
        match = _NEXT_DATA_RE.search(html)
        if not match:
            return []
        data = json.loads(match.group(1))
        page = ((data.get("props") or {}).get("pageProps") or {}).get("pageInformation") or {}
        contents = (page.get("searchResults") or {}).get("contents") or {}
        offers = contents.get("offers") or []
        # Die Gültigkeit steht am Prospekt, nicht am einzelnen Angebot
        validity = {}
        for brochure in contents.get("brochures") or []:
            content = (brochure or {}).get("content") or {}
            if content.get("id"):
                validity[content["id"]] = (content.get("validFrom"), content.get("validUntil"))
        return [
            _normalize_mp(item, validity.get((item.get("parentContent") or {}).get("id")))
            for item in offers
            if isinstance(item, dict)
        ]

    async def _update_meinprospekt(self, session):
        now = datetime.now(timezone.utc)
        if self._mp_time is not None and now - self._mp_time < MP_INTERVAL:
            return
        limit = asyncio.Semaphore(4)

        async def fetch(query):
            async with limit:
                try:
                    return query, await self._fetch_meinprospekt(session, query), True
                except Exception as e:
                    _LOGGER.warning(f"meinprospekt error for {query}: {e}")
                    return query, self._mp_cache.get(query, []), False

        fetched = await asyncio.gather(*(fetch(product["query"]) for product in self.products))
        self._mp_cache = {query: offers for query, offers, _ in fetched}
        if any(ok for _, _, ok in fetched):
            self._mp_time = now

    def _retailer_ok(self, offer):
        if not self.retailers:
            return True
        names = [(adv.get("name") or "").lower() for adv in (offer.get("advertisers") or [])]
        return any(r in name for r in self.retailers for name in names)

    async def async_search(self, query):
        """Freie Suche in beiden Quellen, mit demselben Händlerfilter."""
        async with aiohttp.ClientSession() as session:
            params = {"as": "web", "limit": 24, "offset": 0, "q": query, "zipCode": self.zip_code}
            try:
                async with session.get(
                    API_URL, headers=HEADERS, params=params, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    offers = [o for o in data.get("results", []) if self._retailer_ok(o)]
            except Exception as e:
                _LOGGER.warning(f"Marktguru search error for {query}: {e}")
                offers = []
            try:
                extra = await self._fetch_meinprospekt(session, query)
            except Exception as e:
                _LOGGER.warning(f"meinprospekt search error for {query}: {e}")
                extra = []
        covered = {
            _first_word(adv.get("name")) for o in offers for adv in (o.get("advertisers") or [])
        }
        seen = set()
        for offer in extra:
            retailer = offer["advertisers"][0].get("name")
            if _first_word(retailer) in covered or not self._retailer_ok(offer):
                continue
            key = (retailer, offer["product"].get("name"), offer.get("price"))
            if key not in seen:
                seen.add(key)
                offers.append(offer)
        return [_offer_summary(o) for o in offers[:40]]

    async def _async_update_data(self):
        results = {}
        async with aiohttp.ClientSession() as session:
            for product in self.products:
                query = product["query"]
                params = {
                    "as": "web",
                    "limit": 24,
                    "offset": 0,
                    "q": query,
                    "zipCode": self.zip_code,
                }
                try:
                    async with session.get(API_URL, headers=HEADERS, params=params, timeout=10) as resp:
                        resp.raise_for_status()
                        data = await resp.json()
                        results[query] = [
                            offer for offer in data.get("results", [])
                            if self._retailer_ok(offer)
                        ]
                except Exception as e:
                    _LOGGER.error(f"Marktguru API error for {query}: {e}")
                    results[query] = []

            await self._update_meinprospekt(session)

        # Händler, die Marktguru schon liefert, nicht doppelt aus meinprospekt übernehmen
        covered = {
            _first_word(adv.get("name"))
            for offers in results.values()
            for offer in offers
            for adv in (offer.get("advertisers") or [])
        }
        for product in self.products:
            query = product["query"]
            seen = set()
            for offer in self._mp_cache.get(query, []):
                retailer = offer["advertisers"][0].get("name")
                if _first_word(retailer) in covered or not self._retailer_ok(offer):
                    continue
                key = (retailer, offer["product"].get("name"), offer.get("price"))
                if key in seen:
                    continue
                seen.add(key)
                results.setdefault(query, []).append(offer)
        return results

class MarktguruSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, name, query, idx, zip_code):
        super().__init__(coordinator)
        self._name = f"Marktguru {name} {idx+1} ({zip_code})"
        self._query = query
        self._idx = idx
        self._zip_code = zip_code

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return f"marktguru_{self._zip_code}_{self._query.replace(' ', '_').lower()}_{self._idx}"

    @property
    def state(self):
        offers = self.coordinator.data.get(self._query, [])
        if len(offers) > self._idx:
            return offers[self._idx].get("price")
        return None

    @property
    def extra_state_attributes(self):
        offers = self.coordinator.data.get(self._query, [])
        if len(offers) > self._idx:
            offer = offers[self._idx]
            price = offer.get("price")
            old_price = offer.get("oldPrice")
            reference_price = offer.get("referencePrice")
            vergleichspreis = (
                f"{old_price}€" if old_price else (f"{reference_price}€" if reference_price else "-.-")
            )
            angebotspreis = f"{price}€" if price else None

            # Von- und Bis-Datum (ISO aus API)
            valid_from = offer.get("validityDates", [{}])[0].get("from") if offer.get("validityDates") else None
            valid_to = offer.get("validityDates", [{}])[0].get("to") if offer.get("validityDates") else None

            # Datum menschenlesbar (ohne Jahr)
            von_datum = "-"
            if valid_from:
                try:
                    dt_from = datetime.fromisoformat(valid_from.replace("Z", "+00:00"))
                    von_datum = dt_from.strftime("%d.%m.")
                except Exception:
                    von_datum = valid_from

            bis_datum = "-"
            if valid_to:
                try:
                    dt_to = datetime.fromisoformat(valid_to.replace("Z", "+00:00"))
                    bis_datum = dt_to.strftime("%d.%m.")
                except Exception:
                    bis_datum = valid_to

            # Restlaufzeit in Tagen
            restlaufzeit_tage = "-"
            if valid_to:
                try:
                    dt = datetime.fromisoformat(valid_to.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    diff = (dt - now).days
                    restlaufzeit_tage = diff if diff >= 0 else 0
                except Exception:
                    restlaufzeit_tage = "-"

            # Produktbild-URL extrahieren
            produkt_bild_url = None
            produkt = offer.get("product", {})
            if produkt and "images" in produkt and isinstance(produkt["images"], list) and len(produkt["images"]) > 0:
                produkt_bild_url = produkt["images"][0].get("url")
            elif "images" in offer and isinstance(offer["images"], dict) and "url" in offer["images"]:
                produkt_bild_url = offer["images"]["url"]

            # Ergänzt: results-id und image-url
            result_id = offer.get("id")
            result_image_url = offer.get("_image_url")
            if result_id:
                result_image_url = f"https://mg2de.b-cdn.net/api/v1/offers/{result_id}/images/default/0/small.webp"

            return {
                "price": angebotspreis,
                "old_price": old_price,
                "reference_price": reference_price,
                "vergleichspreis": vergleichspreis,
                "description": offer.get("description"),
                "brand_name": offer.get("brand", {}).get("name") if offer.get("brand") else None,
                "product_name": offer.get("product", {}).get("name") if offer.get("product") else None,
                "retailer_name": offer.get("advertisers", [{}])[0].get("name") if offer.get("advertisers") else None,
                "valid_from": valid_from,
                "valid_to": valid_to,
                "von_datum": von_datum,
                "bis_datum": bis_datum,
                "restlaufzeit_tage": restlaufzeit_tage,
                "produkt_bild_url": produkt_bild_url,
                "result_id": result_id,
                "result_image_url": result_image_url,
                "zip_code": self._zip_code,
                "source": offer.get("_source", "marktguru"),
                "link": offer.get("_link")
                or (f"https://www.marktguru.de/offers/{result_id}" if result_id else None),
                "grundpreis": reference_price,
                "grundpreis_einheit": _base_unit((offer.get("unit") or {}).get("shortName")),
            }
        return {}

class MarktguruQueriesSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, products, zip_code):
        super().__init__(coordinator)
        self._zip_code = zip_code

    @property
    def name(self):
        return f"Marktguru Suchbegriffe ({self._zip_code})"

    @property
    def unique_id(self):
        return f"marktguru_queries_{self._zip_code}"

    @property
    def state(self):
        # Anzahl der Suchbegriffe; die Liste selbst steht im Attribut "queries"
        # (als Zustand wäre sie bei vielen Begriffen länger als die erlaubten 255 Zeichen)
        return len(self.coordinator.products)

    @property
    def extra_state_attributes(self):
        # Auch als Liste verfügbar
        return {
            "queries": [product["query"] for product in self.coordinator.products],
            "zip_code": self._zip_code,
        }


class MarktguruSearchSensor(SensorEntity):
    """Sucht nach dem Text im Helfer SEARCH_ENTITY und stellt die Treffer als Attribut bereit."""

    _attr_should_poll = False
    _unrecorded_attributes = frozenset({"results"})

    def __init__(self, coordinator, zip_code):
        self._coordinator = coordinator
        self._zip_code = zip_code
        self._query = ""
        self._done_query = ""
        self._results = []

    @property
    def name(self):
        return f"Marktguru Suche ({self._zip_code})"

    @property
    def unique_id(self):
        return f"marktguru_search_{self._zip_code}"

    @property
    def icon(self):
        return "mdi:magnify"

    @property
    def state(self):
        return len(self._results)

    @property
    def extra_state_attributes(self):
        return {"query": self._done_query, "results": self._results, "zip_code": self._zip_code}

    async def async_added_to_hass(self):
        self.async_on_remove(
            async_track_state_change_event(self.hass, [SEARCH_ENTITY], self._handle_change)
        )
        current = self.hass.states.get(SEARCH_ENTITY)
        if current is not None:
            self.hass.async_create_task(self._search(current.state))

    @callback
    def _handle_change(self, event):
        new_state = event.data.get("new_state")
        self.hass.async_create_task(self._search(new_state.state if new_state else ""))

    async def _search(self, text):
        query = (text or "").strip()
        if query in ("unknown", "unavailable"):
            query = ""
        self._query = query
        results = await self._coordinator.async_search(query) if query else []
        if self._query != query:
            return  # inzwischen wurde etwas anderes gesucht
        self._results = results
        self._done_query = query
        self.async_write_ha_state()
