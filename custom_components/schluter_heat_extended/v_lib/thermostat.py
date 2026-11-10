# Adapted from Adam Michaleski's MIT-licensed py-schluter thermostat model.
# Changes: simplified and extended device field mapping for this integration.
# Original: https://github.com/prairieapps/py-schluter

import json
import logging

_LOGGER = logging.getLogger(__name__)

class Thermostat:
    def __init__(self, data):
        self.data = data
        self.serial_number = data.get("SerialNumber")
        self.name = data.get("Room") or data.get("GroupName") or "BATHROOM_1"
        
        # Mapping to the exact keys from your "Preview" tab
        self.temperature = data.get("Temperature")    # Was 2250
        self.set_point_temp = data.get("SetPointTemp") # Was 2222
        self.min_temp = data.get("MinTemp") or 500          # Was 500
        self.max_temp = data.get("MaxTemp") or 4000          # Was 4000
        
        self.is_online = data.get("Online", True)
        self.is_heating = data.get("Heating", False)

