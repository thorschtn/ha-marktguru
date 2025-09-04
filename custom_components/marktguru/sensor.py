import logging
from datetime import timedelta, datetime, timezone
import aiohttp

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    CoordinatorEntity,
)
from homeassistant.helpers.entity_platform import async_get_platforms
from .const import DOMAIN, CONF_PRODUCTS, CONF_ZIP, API_URL, HEADERS

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(hours=1)

async def async_setup_entry(hass, entry, async_add_entities):
    zip_code = entry.data[CONF_ZIP]
    products = entry.data[CONF_PRODUCTS]

    coordinator = MarktguruDataCoordinator(hass, zip_code, products)
    await coordinator.async_config_entry_first_refresh()

    # Dynamische Verwaltung der Sensoren
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

    # Queries-Sensor als CoordinatorEntity
    queries_sensor_id = f"marktguru_queries_{zip_code}"
    queries_sensor = MarktguruQueriesSensor(coordinator, products, zip_code)
    sensors.append(queries_sensor)
    current_sensors[queries_sensor_id] = queries_sensor

    async_add_entities(sensors, True)

    # Callback für neue und zu entfernende Sensoren
    async def async_new_data():
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

        # Queries-Sensor immer behalten
        new_sensor_ids.add(queries_sensor_id)

        # Entferne Sensoren, die nicht mehr gebraucht werden
        sensors_to_remove = []
        for sensor_id in list(current_sensors.keys()):
            if sensor_id not in new_sensor_ids:
                sensors_to_remove.append(sensor_id)

        if new_entities:
            async_add_entities(new_entities)

        if sensors_to_remove:
            platforms = async_get_platforms(hass, DOMAIN)
            for platform in platforms:
                platform.async_remove_entities(sensors_to_remove)
            for sensor_id in sensors_to_remove:
                current_sensors.pop(sensor_id, None)

    coordinator.async_add_listener(async_new_data)

class MarktguruDataCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, zip_code, products):
        super().__init__(
            hass,
            _LOGGER,
            name=f"Marktguru Coordinator {zip_code}",
            update_interval=SCAN_INTERVAL,
        )
        self.zip_code = zip_code
        self.products = products

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
                        results[query] = data.get("results", [])
                except Exception as e:
                    _LOGGER.error(f"Marktguru API error for {query}: {e}")
                    results[query] = []
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
            result_image_url = None
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
        # Kommagetrennte Liste aller Queries (aus den aktuellen Produkten des Coordinators)
        return ", ".join(product["query"] for product in self.coordinator.products)

    @property
    def extra_state_attributes(self):
        # Auch als Liste verfügbar
        return {
            "queries": [product["query"] for product in self.coordinator.products],
            "zip_code": self._zip_code,
        }
