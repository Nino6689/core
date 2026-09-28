"""Base entity for the Anycubic integration."""

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import AnycubicCoordinator


class AnycubicEntity(CoordinatorEntity[AnycubicCoordinator]):
    """Base class for Anycubic entities."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: AnycubicCoordinator, description: EntityDescription
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        connection = coordinator.connection
        self._attr_unique_id = f"{connection.device_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, connection.device_id)},
            connections=(
                {(CONNECTION_NETWORK_MAC, connection.mac)} if connection.mac else set()
            ),
            manufacturer=MANUFACTURER,
            model=connection.model,
            model_id=str(connection.model_id),
            name=coordinator.data.printer_name or connection.model_name,
            serial_number=connection.serial,
            sw_version=coordinator.data.firmware_version,
        )
