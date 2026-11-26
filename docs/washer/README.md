# Washer needs-dryer (Washermonitor + Home Assistant)

Where things live (homelab, namespace `home`; HA and ESPHome config are PVC-backed, so git is
made the source of truth with ConfigMaps mounted over the PVC):

| What | Git path | Reaches the cluster as |
|---|---|---|
| ESPHome device config | `kubernetes/apps/home/esphome/config/washermonitor.yaml` | ConfigMap `esphome-washermonitor`, subPath-mounted at `/config/washermonitor.yaml` |
| Previous YAML (flashed Jan 2025) | `kubernetes/apps/home/esphome/config/washermonitor.yaml.bak-20261125` | backup only; also `/config/washermonitor.yaml.bak-20261125` on the ESPHome PVC |
| HA package (helpers, automations, scripts) | `kubernetes/apps/home/home-assistant/app/packages/washer.yaml` | ConfigMap `ha-packages` mounted at `/config/packages` |

One-time manual steps that are NOT in git (live only on the HA PVC `.storage`/config):
- `configuration.yaml`: `homeassistant: packages: !include_dir_named packages` (backup `configuration.yaml.bak-20261125-washer`)
- ESPHome integration option "Allow the device to perform Home Assistant actions" enabled for
  `washermonitor` (backup `.storage/core.config_entries.bak-20261125-washer`). UI path: Settings > Devices & services > ESPHome > Washermonitor > Configure.

## Detection (on the ESP)
1-minute mean (60 x 1 s samples) of `Washing Machine Vibration`; start after 10 continuous
minutes above 0.25 g, hold between 0.20 and 0.25, OFF below 0.20 g, then a 10 min off delay. The start
delay rejects the prolonged signal decay observed after the stick was handled. The 10 min off delay
covers the 6-8 min pauses seen inside a load; the longest in-load gap in the recorder data is 8 min.
The HA automation only flags a run that lasted >= 10 min and leaves any existing `needs_dryer`
finish timestamp unchanged until that pending load is cleared. The device is an original M5StickC
with an 80x160 ST7735S display, powered by the AXP192 LCD rails.

## Replay of recorder history (scripts: `export_vib_1m.py`, `replay.py`)
Raw 1 s vibration states 2026-11-14 .. 2026-11-25, 1-min means, same thresholds and 10 min delay:
last 72 h (from Sun 11-22 18:04): old logic = 22 ON transitions; new logic = 13 runs, 9 of them
>= 10 min (flag set), 4 short bursts (3-7 min, no flag). See the task report for the list.
