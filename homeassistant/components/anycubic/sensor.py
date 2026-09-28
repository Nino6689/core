"""Sensor platform for the Anycubic integration."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import override

from anycubic_lan import Job, PrinterState, PrinterStatus, PrintStatus, SpeedMode

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .coordinator import AnycubicConfigEntry
from .entity import AnycubicEntity

# Push-based; entities do not poll.
PARALLEL_UPDATES = 0

STATUS_OPTIONS = [
    status.value for status in PrinterStatus if status is not PrinterStatus.UNKNOWN
]
JOB_STATUS_IDLE = "idle"
JOB_STATUS_OPTIONS = [status.name.lower() for status in PrintStatus] + [JOB_STATUS_IDLE]
SPEED_MODE_OPTIONS = [mode.name.lower() for mode in SpeedMode]
NO_ERROR = "none"


@dataclass(frozen=True, kw_only=True)
class AnycubicSensorEntityDescription(SensorEntityDescription):
    """Describes an Anycubic sensor."""

    value_fn: Callable[[PrinterState], StateType | datetime]


@dataclass(frozen=True, kw_only=True)
class AnycubicJobSensorEntityDescription(SensorEntityDescription):
    """Describes an Anycubic sensor that reads the current job."""

    value_fn: Callable[[Job], StateType | datetime]


def _status(state: PrinterState) -> str | None:
    if state.status is PrinterStatus.UNKNOWN:
        return None
    return state.status.value


def _job_status(state: PrinterState) -> str | None:
    if state.job is None:
        return JOB_STATUS_IDLE
    if state.job.print_status is None:
        return None
    return state.job.print_status.name.lower()


def _speed_mode(state: PrinterState) -> str | None:
    if state.speed_mode is None:
        return None
    return state.speed_mode.name.lower()


def _last_error_code(state: PrinterState) -> str:
    if state.last_error_code is None:
        return NO_ERROR
    return str(state.last_error_code)


def _job_end_time(job: Job) -> datetime | None:
    if job.remain_time is None or job.is_finished:
        return None
    # Whole minutes, so the value does not change with every report.
    now = dt_util.utcnow().replace(second=0, microsecond=0)
    return now + timedelta(minutes=job.remain_time)


SENSORS: tuple[AnycubicSensorEntityDescription, ...] = (
    AnycubicSensorEntityDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=STATUS_OPTIONS,
        value_fn=_status,
    ),
    AnycubicSensorEntityDescription(
        key="nozzle_temperature",
        translation_key="nozzle_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state: state.temperatures.nozzle,
    ),
    AnycubicSensorEntityDescription(
        key="nozzle_target_temperature",
        translation_key="nozzle_target_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state: state.temperatures.nozzle_target,
    ),
    AnycubicSensorEntityDescription(
        key="bed_temperature",
        translation_key="bed_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state: state.temperatures.bed,
    ),
    AnycubicSensorEntityDescription(
        key="bed_target_temperature",
        translation_key="bed_target_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state: state.temperatures.bed_target,
    ),
    AnycubicSensorEntityDescription(
        key="fan_speed",
        translation_key="fan_speed",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.fans.fan_speed_pct,
    ),
    AnycubicSensorEntityDescription(
        key="auxiliary_fan_speed",
        translation_key="auxiliary_fan_speed",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.fans.aux_fan_speed_pct,
    ),
    AnycubicSensorEntityDescription(
        key="job_status",
        translation_key="job_status",
        device_class=SensorDeviceClass.ENUM,
        options=JOB_STATUS_OPTIONS,
        value_fn=_job_status,
    ),
    AnycubicSensorEntityDescription(
        key="speed_mode",
        translation_key="speed_mode",
        device_class=SensorDeviceClass.ENUM,
        options=SPEED_MODE_OPTIONS,
        value_fn=_speed_mode,
    ),
    AnycubicSensorEntityDescription(
        key="last_error_code",
        translation_key="last_error_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_last_error_code,
    ),
)

CHAMBER_SENSOR = AnycubicSensorEntityDescription(
    key="chamber_temperature",
    translation_key="chamber_temperature",
    device_class=SensorDeviceClass.TEMPERATURE,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda state: state.temperatures.chamber,
)

JOB_SENSORS: tuple[AnycubicJobSensorEntityDescription, ...] = (
    AnycubicJobSensorEntityDescription(
        key="job_progress",
        translation_key="job_progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda job: job.progress,
    ),
    AnycubicJobSensorEntityDescription(
        key="job_current_layer",
        translation_key="job_current_layer",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda job: job.current_layer,
    ),
    AnycubicJobSensorEntityDescription(
        key="job_total_layers",
        translation_key="job_total_layers",
        value_fn=lambda job: job.total_layers,
    ),
    AnycubicJobSensorEntityDescription(
        key="job_elapsed",
        translation_key="job_elapsed",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda job: job.print_time,
    ),
    AnycubicJobSensorEntityDescription(
        key="job_remaining",
        translation_key="job_remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda job: job.remain_time,
    ),
    AnycubicJobSensorEntityDescription(
        key="job_end_time",
        translation_key="job_end_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_job_end_time,
    ),
    AnycubicJobSensorEntityDescription(
        key="job_name",
        translation_key="job_name",
        value_fn=lambda job: job.name,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnycubicConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Anycubic sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        AnycubicSensor(coordinator, description) for description in SENSORS
    ]
    entities.extend(
        AnycubicJobSensor(coordinator, description) for description in JOB_SENSORS
    )
    async_add_entities(entities)

    # Printers without a chamber report 0; add the sensor once a real reading
    # has been seen.
    chamber_added = False

    @callback
    def _async_add_chamber_sensor() -> None:
        nonlocal chamber_added
        if chamber_added or not coordinator.data.has_chamber:
            return
        chamber_added = True
        async_add_entities([AnycubicSensor(coordinator, CHAMBER_SENSOR)])

    _async_add_chamber_sensor()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_chamber_sensor))


class AnycubicSensor(AnycubicEntity, SensorEntity):
    """A sensor reading the printer state."""

    entity_description: AnycubicSensorEntityDescription

    @property
    @override
    def native_value(self) -> StateType | datetime:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(self.coordinator.data)


class AnycubicJobSensor(AnycubicEntity, SensorEntity):
    """A sensor reading the current job; unknown when there is none."""

    entity_description: AnycubicJobSensorEntityDescription

    @property
    @override
    def native_value(self) -> StateType | datetime:
        """Return the state of the sensor."""
        if (job := self.coordinator.data.job) is None:
            return None
        return self.entity_description.value_fn(job)
