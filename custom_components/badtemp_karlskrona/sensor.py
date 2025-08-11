"""
Water Temperature Sensors for Karlskrona Swim Areas Integration

Cleaned up sensor.py - Main sensor platform implementation.
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import aiohttp
import voluptuous as vol

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import API_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(minutes=5)

SWEDISH_CHAR_MAP = {
    "å": "a",
    "ä": "a", 
    "ö": "o",
    "Å": "A",
    "Ä": "A",
    "Ö": "O",
}


def normalize_swedish_chars(text: str) -> str:
    """Normalize Swedish characters for entity IDs."""
    for swedish_char, replacement in SWEDISH_CHAR_MAP.items():
        text = text.replace(swedish_char, replacement)
    return text


class KarlskronaSwimDataCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the Karlskrona swim areas API."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Karlskrona Swim Areas",
            update_interval=UPDATE_INTERVAL,
        )
        self.session = async_get_clientsession(hass)

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from API endpoint."""
        _LOGGER.debug(f"Fetching data from: {API_URL}")
        
        try:
            async with self.session.get(API_URL, timeout=10) as response:
                _LOGGER.debug(f"HTTP Response status: {response.status}")
                _LOGGER.debug(f"Response headers: {dict(response.headers)}")
                
                if response.status != 200:
                    raise UpdateFailed(f"Error fetching data: HTTP {response.status}")
                
                # Get the raw bytes first
                response_bytes = await response.read()
                _LOGGER.debug(f"Response bytes length: {len(response_bytes)}")
                
                if len(response_bytes) == 0:
                    raise UpdateFailed("API returned empty response")
                
                # Log first 100 bytes in hex to see what we're getting
                _LOGGER.debug(f"First 100 bytes (hex): {response_bytes[:100].hex()}")
                _LOGGER.debug(f"First 100 bytes (repr): {repr(response_bytes[:100])}")
                
                # Try to decode with different encodings
                response_text = None
                encodings_to_try = ['utf-16', 'utf-16-le', 'utf-16-be', 'utf-8', 'iso-8859-1', 'windows-1252', 'utf-8-sig']
                
                for encoding in encodings_to_try:
                    try:
                        response_text = response_bytes.decode(encoding)
                        _LOGGER.debug(f"Successfully decoded response using {encoding}")
                        break
                    except UnicodeDecodeError:
                        continue
                
                if response_text is None:
                    raise UpdateFailed("Could not decode API response with any supported encoding")
                
                _LOGGER.debug(f"Response text length: {len(response_text)}")
                _LOGGER.debug(f"Response text preview (first 200 chars): {response_text[:200]}")
                
                # Check if response is actually empty or whitespace
                if not response_text.strip():
                    raise UpdateFailed("API returned empty or whitespace-only response")
                
                # Parse JSON
                try:
                    data = json.loads(response_text.strip())
                except json.JSONDecodeError as json_err:
                    _LOGGER.error(f"JSON decode error: {json_err}")
                    _LOGGER.error(f"Response text (first 500 chars): {response_text[:500]}")
                    raise UpdateFailed(f"Error parsing JSON: {json_err}") from json_err
                
                _LOGGER.debug(f"Successfully fetched data from API. Keys: {data.keys()}")
                
                # Validate the expected structure
                if "Payload" not in data:
                    _LOGGER.error("API response missing 'Payload' key")
                    _LOGGER.debug(f"Available keys: {list(data.keys())}")
                    raise UpdateFailed("Invalid API response structure - missing Payload")
                
                if "swimAreas" not in data["Payload"]:
                    _LOGGER.error("API response missing 'swimAreas' in Payload")
                    _LOGGER.debug(f"Payload keys: {list(data['Payload'].keys())}")
                    raise UpdateFailed("Invalid API response structure - missing swimAreas")
                
                swim_areas = data["Payload"]["swimAreas"]
                _LOGGER.info(f"Found {len(swim_areas)} swim areas in API response")
                
                return data
                
        except asyncio.TimeoutError as err:
            _LOGGER.error("Timeout while fetching data from API")
            raise UpdateFailed("Timeout while fetching data") from err
        except aiohttp.ClientError as err:
            _LOGGER.error(f"HTTP client error: {err}")
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except Exception as err:
            _LOGGER.error(f"Unexpected error during API fetch: {type(err).__name__}: {err}")
            raise UpdateFailed(f"Unexpected error: {err}") from err


async def async_setup_platform(
    hass: HomeAssistant,
    config,
    async_add_entities: AddEntitiesCallback,
    discovery_info=None,
) -> None:
    """Set up Karlskrona swim area sensors from YAML configuration."""
    _LOGGER.info("Setting up Karlskrona swim area sensors")
    
    coordinator = KarlskronaSwimDataCoordinator(hass)
    
    try:
        # Fetch initial data for YAML setup
        await coordinator.async_refresh()
        
        entities = []
        
        if coordinator.data and "Payload" in coordinator.data and "swimAreas" in coordinator.data["Payload"]:
            for area_data in coordinator.data["Payload"]["swimAreas"]:
                entities.append(KarlskronaSwimTemperatureSensor(coordinator, area_data))
                _LOGGER.debug(f"Added sensor for: {area_data['nameArea']}")
        else:
            _LOGGER.warning("No swim areas found in API response or invalid data structure")
            return
        
        if entities:
            async_add_entities(entities, update_before_add=True)
            _LOGGER.info(f"Successfully added {len(entities)} swim area sensors")
        else:
            _LOGGER.warning("No entities to add")
            
    except Exception as err:
        _LOGGER.error(f"Failed to set up Karlskrona swim sensors: {err}")
        raise


class KarlskronaSwimTemperatureSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Karlskrona swim area temperature sensor."""

    def __init__(
        self, 
        coordinator: KarlskronaSwimDataCoordinator, 
        area_data: Dict[str, Any]
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        
        self._area_name = area_data["nameArea"]
        self._area_id = normalize_swedish_chars(self._area_name.lower())
        
        # Set up entity attributes
        self._attr_name = f"Water Temperature {self._area_name}"
        self._attr_unique_id = f"karlskrona_swim_temp_{self._area_id}"
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_icon = "mdi:thermometer-water"
        
        # Set up device info to group entities properly
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"swim_area_{self._area_id}")},
            name=f"Karlskrona {self._area_name}",
            manufacturer="Karlskrona Municipality",
            model="Swim Area Temperature Sensor",
            sw_version="1.0",
            entry_type="service",
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success and self._get_area_data() is not None

    @property
    def native_value(self) -> Optional[float]:
        """Return the native value of the sensor."""
        area_data = self._get_area_data()
        if not area_data:
            return None
            
        temp = area_data.get("temperatureWater")
        if isinstance(temp, (int, float)):
            return round(float(temp), 1)
        return None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        area_data = self._get_area_data()
        if not area_data:
            return {}

        attributes = {
            "area_name": self._area_name,
            "last_updated": self._parse_timestamp(area_data.get("timeStamp")),
        }

        # Add coordinates if available
        geometry = area_data.get("geometryArea", {})
        if geometry.get("x") and geometry.get("y"):
            attributes.update({
                "latitude": float(geometry["y"]),
                "longitude": float(geometry["x"]),
            })

        return attributes

    def _get_area_data(self) -> Optional[Dict[str, Any]]:
        """Get data for this specific area from coordinator data."""
        if not self.coordinator.data:
            return None
            
        swim_areas = self.coordinator.data.get("Payload", {}).get("swimAreas", [])
        
        for area in swim_areas:
            if normalize_swedish_chars(area["nameArea"].lower()) == self._area_id:
                return area
        
        return None

    def _parse_timestamp(self, timestamp_str: Optional[str]) -> Optional[str]:
        """Parse timestamp string into ISO format."""
        if not timestamp_str:
            return None
            
        try:
            # Remove microseconds if present
            clean_timestamp = timestamp_str.split('.')[0]
            dt = datetime.fromisoformat(clean_timestamp.replace('T', ' '))
            return dt.isoformat()
        except (ValueError, AttributeError) as err:
            _LOGGER.warning("Could not parse timestamp %s: %s", timestamp_str, err)
            return None
