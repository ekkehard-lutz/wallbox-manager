# Third-party notices

Portions of this integration and its lifecycle/discovery tests are adapted from
[lbbrhzn/ocpp](https://github.com/lbbrhzn/ocpp), pinned commit
`848407c11ff659ce59779a99ce69984bbb0e3ce1`.

Original files: `custom_components/ocpp/api.py`, `chargepoint.py`, `ocppv16.py`,
`ocppv201.py`, `__init__.py`, `config_flow.py`, `sensor.py`; tests
`test_reconnect_lifecycle.py`, `test_initial_start_lifecycle.py`,
`test_v201_smart_charging_probe.py`, `test_v201_probe_timeout.py`.

Changes: isolated protocol adapters and captured socket/task owners; strict
negotiation; generation-fenced generic snapshots; read-only bounded discovery;
read-only HA platform lifecycle, diagnostic descriptions, DeviceInfo and push
subscription cleanup adapted to generic snapshots; ACK-only MeterValues,
NotifyEvent and TransactionEvent response patterns; sampled-value buckets, unit/
multiplier normalization, phase selection, status mapping and HA measurement
class/unit patterns adapted to immutable scoped observations. No upstream phase
aggregation, connector flattening or transaction/control policy is inherited;
no inherited charging policy,
services, measurement entity model or automatic availability.
Tests use Wallbox Manager contracts and fake/local WebSocket peers without the
upstream HA fixture suite. See `docs/upstream-ocpp-analysis.md` in the repository
for the original-to-local mapping and detailed adoption record.

MIT License

Copyright (c) 2021 lbbrhzn

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
