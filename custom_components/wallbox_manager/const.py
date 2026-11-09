"""Integration identity and passive endpoint defaults."""

DOMAIN = "wallbox_manager"
ENTRY_VERSION = 3
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 9000
PLATFORMS = ("sensor", "binary_sensor", "number", "select", "text", "switch", "button")

CAPABILITIES = {
    "SupportedPhaseModes": "supported_phases",
    "MinimumCurrent": "minimum_current",
    "CurrentStep": "current_step",
    "PhaseSwitchingSupported": "phase_switching",
    "MaximumCurrent": "maximum_current",
    "MaximumAsymmetryCurrent": "maximum_asymmetry_current",
    "ZeroCurrentSupported": "zero_current",
    "ChargingEnableDisableSupported": "enable_disable",
}
