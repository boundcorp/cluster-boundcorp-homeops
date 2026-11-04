# Frigate Coral detection investigation

## Root cause

Configuration failure, not missing hardware or container access: Frigate 0.18.2 defaults `DetectConfig.enabled` to `false`, while the Git-managed configuration omitted `detect.enabled` globally and for both cameras. The Coral detector process was loaded but idle because neither camera supplied detection work.

## Before-change evidence

- Flux Kustomization `cluster-apps-home-frigate` and HelmRelease `home/frigate` were Ready; deployed image was `ghcr.io/blakeblackshear/frigate:0.18.2`, API version `0.18.2-cfa08c5`.
- Pod `frigate-7bbb98b459-kfn8z` was Ready on `nova`, with zero restarts. Both camera streams processed approximately 10 fps, but both reported `detection_enabled=false` and `detection_fps=0`.
- The effective detector was `coral`, `type=edgetpu`, `device=usb`, model `/edgetpu_model.tflite`; PID 634 had `libedgetpu.so.1.0` loaded. Reported inference speed was 10 ms, but that idle value alone did not prove actual inference.
- Nova sysfs enumerated Coral `18d1:9302` at USB port `2-5.1`, bus 002/device 004. Kernel history showed the expected firmware transition from `1a6e:089a` to `18d1:9302`; two later reset entries did not explain disabled detection.
- The container ran as UID 0 with `privileged: true`, the entire host `/dev/bus/usb` mounted, and `/dev/bus/usb/002/004` present with mode 0664. It did not use a fragile single-device mount. No Coral device-plugin resource was advertised or requested; this direct privileged USB arrangement does not require one.
- `/config/.runtime_state.json` was absent. The running image's `/opt/frigate/frigate/config/camera/detect.py` declared `enabled: bool = Field(default=False, ...)`. Parsing the live configuration with that image produced `detect_enabled=false` for both cameras; adding global `detect.enabled: true` produced `true` for both, retaining the same detector/model.
- Current retained logs started after the pod's original startup, so the original `TPU found` line was not retained. Previous-container logs were unavailable because there had been no container restart; pod events were empty.

## Fix and validation

Set global `detect.enabled: true` in `kubernetes/apps/home/frigate/app/config/config.yml`. Retain the existing USB mount, privileged access, detector/model defaults and node selector: the hardware is already attached to the selected node.

Before deployment, `kubectl kustomize kubernetes/apps/home/frigate/app` rendered successfully and `kubectl --context home apply --dry-run=server -f -` accepted all four rendered objects. The running Frigate parser demonstrated disabled-before/enabled-after for both cameras. Deployment must proceed through Git/Flux, not a direct apply.

After Flux deploys the configuration and Frigate restarts, verify `TPU found` in fresh logs, a Ready pod on nova, both cameras reporting `detection_enabled=true` with healthy camera/process fps, and actual Coral inference near 5–20 ms while detections are being processed. A quiet scene can legitimately have zero instantaneous detection fps; the idle initial inference-speed value is insufficient proof. No camera URLs, credentials or images are needed for verification.
