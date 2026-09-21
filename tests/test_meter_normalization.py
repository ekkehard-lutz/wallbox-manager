"""Adapter-boundary unit, phase, timestamp and ambiguity regressions."""

from datetime import timedelta
from fractions import Fraction

import pytest

from custom_components.wallbox_manager.core.models import StationId
from custom_components.wallbox_manager.core.telemetry import Quantity
from custom_components.wallbox_manager.protocols.ocpp.common.metering import (
    meter_observations,
)


def parse(now, samples, *, legacy=False, timestamp=None):
    return meter_observations(
        StationId("a"),
        [{"timestamp": timestamp or now.isoformat(), "sampled_value": samples}],
        "test",
        legacy=legacy,
        received_at=now,
    )


def sample(measurand="Voltage", value=230, phase="L1-N", unit="V", multiplier=0):
    return dict(
        measurand=measurand,
        value=value,
        phase=phase,
        unit_of_measure=dict(unit=unit, multiplier=multiplier),
    )


def test_three_phase_and_total_are_independent(now):
    samples = []
    for n in range(1, 4):
        samples += [
            sample(value=228 + n, phase=f"L{n}-N"),
            sample("Current.Import", n * 2, f"L{n}", "A"),
            sample("Power.Active.Import", n * 1000, f"L{n}", "W"),
        ]
    samples += [
        sample("Power.Active.Import", 6555, None, "W"),
        sample("Energy.Active.Import.Register", 12.5, None, "kWh"),
    ]
    observations = parse(now, samples)
    assert len(observations) == 11
    values = {o.channel.quantity: o.value for o in observations}
    assert values[Quantity.VOLTAGE_L3] == 231
    assert values[Quantity.CURRENT_L2] == 4
    assert values[Quantity.POWER_L2] == 2000
    assert values[Quantity.POWER] == 6555  # Not the sum of reported phases.
    assert values[Quantity.ENERGY] == 12500


@pytest.mark.parametrize(
    "measurand,unit,value,multiplier,expected",
    [
        ("Power.Active.Import", "kW", 1.25, 0, 1250),
        ("Power.Active.Import", "W", 125, -1, Fraction(25, 2)),
        ("Energy.Active.Import.Register", "Wh", 12345, -2, Fraction(2469, 20)),
        ("Energy.Active.Import.Register", "kWh", 2, 3, 2000000),
    ],
)
def test_si_unit_and_multiplier(now, measurand, unit, value, multiplier, expected):
    assert (
        parse(now, [sample(measurand, value, None, unit, multiplier)])[0].value
        == expected
    )


def test_partial_missing_unsupported_and_ambiguous_phases(now):
    values = parse(
        now,
        [
            sample(),
            sample(phase="L1-L2"),
            sample(phase="N"),
            sample("Frequency", 50, None, "Hz"),
            sample(phase=None),
        ],
    )
    assert len(values) == 1
    assert values[0].channel.quantity == Quantity.VOLTAGE_L1
    assert parse(now, []) == ()


@pytest.mark.parametrize(
    "change",
    [
        {"value": -1},
        {"value": True},
        {"value": float("inf")},
        {"value": float("nan")},
        {"value": "230"},
        {"unit_of_measure": {"unit": "Wh"}},
        {"unit_of_measure": {"unit": "V", "multiplier": 100000}},
        {"unit_of_measure": {"unit": "V", "multiplier": 1.5}},
        {"location": "Inlet"},
        {"format": "SignedData"},
        {"context": "Other"},
    ],
)
def test_invalid_known_channel_is_unknown_not_zero(now, change):
    assert parse(now, [{**sample(), **change}])[0].value is None


def test_zero_and_duplicates(now):
    assert parse(now, [sample(value=0)])[0].value == 0
    assert parse(now, [sample(), sample()])[0].value == 230
    for samples in ([sample(), sample(value=240)], [sample(value=240), sample()]):
        assert parse(now, samples)[0].value is None


def test_timestamps_and_expiry(now):
    assert parse(now, [sample()], timestamp="not-time") == ()
    assert parse(now, [sample()], timestamp=now.replace(tzinfo=None).isoformat()) == ()
    assert (
        parse(now, [sample()], timestamp=(now + timedelta(seconds=10)).isoformat())
        == ()
    )
    observation = parse(
        now, [sample()], timestamp=(now - timedelta(seconds=121)).isoformat()
    )[0]
    assert not observation.fresh(now)
    assert observation.received_at == now


def test_legacy_decimal_values_and_defaults(now):
    legacy = {"value": "1.25", "measurand": "Power.Active.Import", "unit": "kW"}
    assert parse(now, [legacy], legacy=True)[0].value == 1250
    assert parse(now, [{"value": "42"}], legacy=True)[0].value == 42
    assert parse(now, [{**legacy, "value": "1/2"}], legacy=True)[0].value is None
    assert parse(now, [{**legacy, "value": "1e100000"}], legacy=True)[0].value is None
    # Missing unit defaults to Wh in OCPP, never an invented voltage unit.
    assert (
        parse(now, [{"value": 230, "measurand": "Voltage", "phase": "L1"}])[0].value
        is None
    )
