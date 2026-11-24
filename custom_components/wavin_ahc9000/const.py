DOMAIN = "wavin_ahc9000"
NAME = "Wavin Floor Heating"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_NOISE_PSK = "noise_psk"
CONF_EXTERNAL_SENSORS = "external_sensors"
DEFAULT_PORT = 6053
TOPOLOGY_NAME = "Wavin Zone Topology"
TOPOLOGY_NAMES = ("Wavin Zone Topology", "Wavin Sentio Topology")
TOPOLOGY_NAME_PREFIXES = TOPOLOGY_NAMES


def is_topology_name(name: str | None) -> bool:
    """Return true for the base topology sensor and per-controller variants."""
    if not name:
        return False
    return any(name == base or name.startswith(f"{base} ") for base in TOPOLOGY_NAME_PREFIXES)
