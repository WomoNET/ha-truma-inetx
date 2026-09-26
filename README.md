# Truma iNet X for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/WomoNET/ha-truma-inetx/actions/workflows/validate.yml/badge.svg)](https://github.com/WomoNET/ha-truma-inetx/actions/workflows/validate.yml)
[![Tests](https://github.com/WomoNET/ha-truma-inetx/actions/workflows/tests.yml/badge.svg)](https://github.com/WomoNET/ha-truma-inetx/actions/workflows/tests.yml)

Local control of a Truma iNet X panel over Bluetooth, without the Truma cloud.
The panel and every appliance behind it (for example the Combi heater) show
up as devices in Home Assistant, and changes made at the panel are pushed to
Home Assistant immediately.

> **Early release.** The protocol and command sequences were examined on a
> panel with a Truma Combi 6. Other appliances use the same protocol, but have
> not been tested yet; see [Other appliances](#other-appliances).

## Features

| Entity | Device | Description |
|---|---|---|
| Room climate (climate) | Panel | Off, heating and ventilation, heating target temperature, room temperature |
| Ventilation (fan) | Heater | Fan level 1 to 10 in ventilation mode |
| Hot water (water heater) | Heater | Off or one of the heater's levels (40, 60, 70 °C) |
| Heating mode (select) | Heater | Comfort or fast |
| Gas, electric heating, diesel (select) | Heater | Energy sources, if the heater reports them |
| Room and water temperature (sensor) | Heater | Measured temperatures |
| Panel temperature (sensor, disabled by default) | Panel | Sensor inside the panel |
| Flame (binary sensor) | Panel | Burner flame |

Entities are created from what the system reports: a gas heater gets no
diesel select, and an appliance that is switched on later gets its entities
when it first reports.

The connection is kept open and restored automatically after a loss: at
increasing intervals while the panel is out of range, and right away when
Home Assistant sees the panel again.

## Requirements

- Home Assistant 2026.9 or newer.
- A Bluetooth adapter or an [ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy/)
  in range of the panel, with an active connection available.

## Installation

### HACS

1. In HACS, open the menu and choose **Custom repositories**.
2. Add `https://github.com/WomoNET/ha-truma-inetx` with the type **Integration**.
3. Install **Truma iNet X (Bluetooth)** and restart Home Assistant.

### Manual

Copy `custom_components/truma_inetx` into the `custom_components` folder of
your Home Assistant configuration and restart Home Assistant.

## Setup

Home Assistant usually discovers the panel on its own and offers it under
**Settings > Devices & services**. Otherwise add the integration
**Truma iNet X (Bluetooth)** there and pick the panel.

Home Assistant pairs with the panel once:

1. Close the Truma iNet X app on phones and tablets nearby.
2. On the iNet X panel, start pairing a new Bluetooth device, as you would
   for the Truma app.
3. While the panel is waiting for a device, select **Submit** in Home
   Assistant.

### Pairing again

The panel keeps a limited number of pairings, so pairing more phones can
remove the one of Home Assistant. When connecting keeps failing although the
panel is in range, Home Assistant asks you to pair again. Follow the same
steps as above.

## Troubleshooting

- **The panel is not found:** make sure the panel is powered and that an
  adapter or proxy is close enough. Close the Truma app, which can occupy the
  panel's connection.
- **Pairing fails:** start pairing mode on the panel again right before
  selecting **Submit**.
- **Connections fail with a local adapter after pairing:** the panel uses
  changing random addresses, which the Linux kernel has to resolve with the
  key exchanged during pairing. Issues with this have been reported for recent
  kernels; an ESPHome Bluetooth proxy avoids the problem.
- For details, enable debug logging for `custom_components.truma_inetx`.

## Other appliances

Heating, hot water and ventilation use command sequences that were verified
on a real system. The heating mode and energy source selects write one of the
options the heater itself offers; these writes have not been verified yet.
Room modes that a panel offers but that were not verified (for example cooling
with a Truma Aventa) are not shown yet.

To help add support for another appliance, open an issue and attach the
integration's diagnostics (**Settings > Devices & services > Truma iNet X >
Download diagnostics**). They contain the full parameter schema of every
appliance, with serial numbers and the address removed.

## Credits

The integration would not exist without the reverse engineering of others:

- [guwo65](https://github.com/guwo65) worked out pairing, message routing and
  the command sequences for heating, hot water and ventilation through a long
  series of careful tests on a real panel.
- [daaaaan](https://github.com/daaaaan/truma-inetx-ble) documented the
  transport, frame format and message types in a protocol reference.

The protocol as implemented here is described in [docs/protocol.md](docs/protocol.md).

## Disclaimer

This project is not affiliated with or endorsed by Truma. It controls a
heating appliance, so check that automations behave as expected before relying
on them. Use at your own risk.

## License

This program is free software: you can redistribute it and/or modify it under
the terms of the [GNU General Public License](LICENSE) as published by the Free
Software Foundation, either version 3 of the License, or (at your option) any
later version.
