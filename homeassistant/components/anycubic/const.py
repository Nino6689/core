"""Constants for the Anycubic integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "anycubic"

MANUFACTURER: Final = "Anycubic"

# The printer answers queries but does not stream every report on its own.
QUERY_INTERVAL: Final = timedelta(seconds=15)

# How long setup and reconnects wait for the printer's first info report.
FIRST_REPORT_TIMEOUT: Final = 15
