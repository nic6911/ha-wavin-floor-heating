# Wavin Floor Heating

Home Assistant integration for Wavin floor-heating controllers running ESPHome:

- **Wavin AHC9000 / Jablotron AC-116** using `wavinahc9000v4`
- **Wavin Sentio** using `sentiov2`

The firmware detects the controller model from the ESPHome topology data. This
integration turns that topology into Home Assistant devices and entities
automatically - no per-zone YAML, no register list, no MQTT.

[![Open in Marketplace](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=nic6911&repository=ha-wavin-floor-heating&category=integration)

> **Dual-controller support:** v0.4.0 supports two AHC9000 controllers on one
> RS-485 bus and one ESPHome device, with one child device per controller. The
> single-AHC path remains backwards compatible.
>
> **External room sensors:** v0.5.0 adds integration-managed external
> temperature sensors; see below.

> **Status:** 0.5.1. The firmware examples track the latest `main` branch.
> AHC9000 non-IR zones and Sentio rooms are validated on real hardware; AHC9000
> IR floor-probe controls need field validation.

---

## Start here

You need two things: the ESP32 firmware and this integration.

### 1. Flash the ESP32

Use the ready-made ESPHome configuration for your controller:

- **[Wavin AHC9000 V4](https://github.com/heinekmadsen/esphome_components/blob/main/components/wavinahc9000v4/README.md)**
- **[Wavin Sentio V2](https://github.com/heinekmadsen/esphome_components/blob/main/components/sentiov2/README.md)**

The short version:

1. Open ESPHome.
2. Create a new device and paste the matching example YAML.
3. Set your Wi-Fi name/password and the correct UART pins for your board.
4. Install it to the ESP32.
5. Wait until the ESPHome device is online.

The firmware automatically finds the zones or rooms. You do not need to edit one
climate entity per channel.

### 2. Install this integration

1. Go to **Settings → Marketplace**.
2. Select **Add from link**.
3. Enter this repository:

   ```text
   https://github.com/nic6911/ha-wavin-floor-heating
   ```

4. If Marketplace asks for a type, choose **Integration**.
5. Open **Wavin Floor Heating** and select **Install**.
6. If Home Assistant asks to restart, restart it.

### 3. Add the device in Home Assistant

Home Assistant normally finds the module automatically after it is online.

1. Wait for a discovered **Wavin AHC9000** or **Wavin Sentio** card, then select
   **Configure**.
2. Confirm the device. You do not need to enter an IP address.
3. If discovery does not appear, go to **Settings → Devices & services →
   Add integration**, search for **Wavin Floor Heating**, and enter the ESP32
   address as a fallback. Leave the API encryption key empty unless you enabled
   ESPHome API encryption.

AHC zones appear as devices named like `Zone (1+2)` or `Zone (16)`. Sentio rooms
appear as `Room 1`, `Room 2`, and so on, or with the room names exposed by the
controller.

That is it. No MQTT broker is needed.

---

## What you get

Common to both controllers:

- One Home Assistant device per zone/room
- Climate entity with heat/off and setpoint control
- Current temperature
- Floor temperature, floor limits and floor control only where an IR floor probe exists
- Child lock switch
- ESP32 Wi-Fi RSSI sensor
- Optional **Sync all rooms/zones to the first controllable room** switch

AHC9000 additionally provides:

- Battery percentage per zone
- A separate two-point `Floor limits` climate entity for IR thermostats
- A `Floor control` switch that enables or disables application of the floor
  limits

Sentio additionally provides:

- Humidity and dew-point sensors
- Low-battery, warning and error binary sensors
- Vacation mode via the `sentio_zone_set_vacation` service

## Combined Sentio rooms

When two Sentio channels share one thermostat, the controller can expose the
writable thermostat in one room and the sensor readings in an adjacent room.
The integration merges these into one Home Assistant device: setpoint and child
lock come from the control room, while temperature, humidity and heating state
come from the sensor room.

## External room sensors

Use **Settings → Devices & services → Wavin Floor Heating → Configure →
External room sensors** to make a zone follow any Home Assistant temperature
sensor instead of its wall thermostat.

Select the controller and zone, then choose a sensor entity. No YAML edit is
required. Removing the mapping immediately hands the zone back to the wall
thermostat. If the selected sensor becomes unavailable, the AHC falls back to
the wall thermostat until it returns.

> **Important:** external sensor control writes the AHC setpoint at most once
> every 60 minutes, with a 0.5 °C deadband. Setpoints are stored in the AHC's
> non-volatile memory. Lowering the interval increases EEPROM wear and should
> only be used for temporary diagnostics.

## Controller behavior

AHC zone setpoints follow the physical wall thermostat. The ESP32 only writes a
setpoint when you change it from Home Assistant.

Sentio readings are filtered so invalid Modbus values (`0`, `327.67` and
out-of-range values) do not create false temperature or humidity spikes in Home
Assistant.

## Sync all rooms/zones

The sync switch copies the first controllable room/zone setpoint to every other
room/zone and keeps them together while enabled. Turn it off to make them
independent again. This works for both AHC9000 and Sentio.

If you rename the master zone device in Home Assistant, the sync switch text
follows that name.

## Troubleshooting

| Problem | Try |
|---|---|
| Cannot connect | Check the ESP32 IP address and that TCP port `6053` is reachable |
| No zones appear | Confirm the firmware exposes a topology text sensor |
| Zones appear but controls fail | Update the firmware and integration together |
| Device is offline | Check Wi-Fi, ESPHome logs, and the RSSI sensor |
| Some AHC zones show no floor controls | The thermostat has no floor probe, or the firmware did not detect it |
| Old zones remain | Reload the integration or restart Home Assistant |

## Compatibility

- Home Assistant 2026.11 or newer
- AHC9000 `wavinahc9000v4` from the latest `main` branch
- Sentio `sentiov2` from the latest `main` branch
- ESPHome topology JSON version `1`
- ESPHome Native API on local TCP port `6053` (no port forwarding)

## Manual installation

Copy `custom_components/wavin_ahc9000` into your Home Assistant
`custom_components` directory, restart Home Assistant, and add the integration.

## Advanced firmware options

For manual per-channel YAML, external room sensors, floor-probe climates, and
other advanced options, see the firmware repositories:

- **[wavinahc9000v4 documentation](https://github.com/heinekmadsen/esphome_components/tree/main/components/wavinahc9000v4)**
- **[sentiov2 documentation](https://github.com/heinekmadsen/esphome_components/tree/main/components/sentiov2)**

Issue tracker:
<https://github.com/nic6911/ha-wavin-floor-heating/issues>
