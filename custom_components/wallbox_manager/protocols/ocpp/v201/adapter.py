"""OCPP 2.0.1 schemas and read-only inventory/boot handlers."""

from ocpp.v201 import ChargePoint

from ..common.inventory import InventoryAdapter


class Adapter(InventoryAdapter, ChargePoint):
    """Use only the 2.0.1 library call/result and validation schema set."""
