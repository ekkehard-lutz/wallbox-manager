"""OCPP 2.1 schema boundary; independently tested discovery subset only."""

from ocpp.v21 import ChargePoint

from ..common.inventory import InventoryAdapter


class Adapter(InventoryAdapter, ChargePoint):
    """Use genuine 2.1 messages/schemas, not a renamed 2.0.1 ChargePoint."""
