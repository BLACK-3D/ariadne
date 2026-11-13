# Three XIAO ESP32-S3 nodes

This pilot targets three **plain Seeed Studio XIAO ESP32-S3 boards**, SKU `113991114`: 8 MB flash, 8 MB PSRAM, native USB-C and the supplied antennas. A Sense camera/microphone expansion, display, battery and soldered headers are unnecessary for this USB-powered experiment.

The preparation utility is offline and uses Python's standard library. Its `verify`, `prepare`, `status` and `plan` actions never open serial, discover hardware, execute a process, make a network request, flash firmware or provision Wi-Fi. A separately invoked USB onboarding utility is an operational step for the hardware's arrival, not a side effect of preparing a kit.

Download the matching firmware, offline flashing tools and updated preparation source from the [XIAO pilot release](https://github.com/BLACK-3D/ariadne/releases/tag/ruview-xiao-s3-headless-v1), including each `.sha256` sidecar. Use its `ariadne-ruview-xiao-tools-source.zip` for these new scripts; the older general AI starter does not establish that they are present. Existing Ariadne users can use their updated source and bundled Python. This separate hardware release leaves the general AI starter's latest-release link unchanged.

## What the kit means

The build is based on RuView revision [`094e1400719ded1283c67d23b9c737f6738a3244`](https://github.com/ruvnet/RuView/tree/094e1400719ded1283c67d23b9c737f6738a3244/firmware/esp32-csi-node), with the shipped Ariadne pilot patch and configuration. It is not an unmodified upstream release or a manufacturer-certified sensing product.

The pilot disables display support, WASM and the onboard LED routine, uses raw CSI instead of the full on-device vitals pipeline, selects native USB, and omits the OTA/configuration HTTP listener. No owner credentials or NVS image are embedded. A new unconfigured board with no SSID remains before Wi-Fi startup until provisioning.

**NVS is preserved.** A reused board can retain old credentials/settings. This kit does not erase them. Use new/unconfigured boards initially; stop for review if USB identification reports an unexpectedly configured board. Do not assume flashing has removed previous network access.

One firmware image set serves all three nodes. Their later private NVS configurations supply distinct node IDs `1`, `2` and `3`. The owner desk initially records unknown COM ports and placements. Labels do not establish room geometry or calibrated coordinates.

The fixed `config/ruview-xiao-firmware.json` admits one particular compiled manifest. An arbitrary caller-supplied hash cannot certify another firmware build. Compilation and matching bytes do not establish USB operation, packet reception, physical safety, accuracy or current occupancy.

## Prepare offline

Use an ordinary extracted Ariadne folder. Extract the matching firmware bundle into `staging/firmware-bundle` inside it. Select its `firmware` subdirectory, which contains exactly the manifest and four images; license/provenance documents stay beside that subdirectory. Keep all inputs/outputs inside Ariadne; E:, traversal, reparse/symlink paths and hardlinked inputs are rejected. Never mix image sets or substitute an S2/C3/C5/C6/display-enabled image.

From **PowerShell** in the Ariadne folder, run one command at a time:

```powershell
& .\runtime\python\python.exe -I -B .\scripts\ruview_nodes.py verify staging/firmware-bundle/firmware
& .\runtime\python\python.exe -I -B .\scripts\ruview_nodes.py prepare staging/firmware-bundle/firmware --name xiao-three-node
& .\runtime\python\python.exe -I -B .\scripts\ruview_nodes.py status --name xiao-three-node
```

Open the generated HTML owner desk under `data/workspace/ruview-kits/xiao-three-node`. Its completed receipt binds the image copies and plan inputs. Existing pending, failed, uncertain or changed kits are never silently overwritten, repaired or replayed. Inspect retained evidence before deliberately preparing another named kit.

Verification checks the sealed manifest, image hashes, ESP32-S3 image headers/checksums, partition-table integrity, segment overlap and flash bounds. The exact layout is:

| File | Offset | Meaning |
|---|---:|---|
| `bootloader.bin` | `0x0` | Matching S3 bootloader |
| `partition-table.bin` | `0x8000` | Matching 8 MB partition layout |
| `ota_data_initial.bin` | `0xf000` | Initial selection metadata; not Wi-Fi NVS |
| `esp32-csi-node.bin` | `0x20000` | Matching headless application |

## Arrival: one board first

1. Inspect an unpowered board, confirm its exact model and carefully attach the supplied antenna. Label it `node-1`; leave the other two unpowered. Use a USB **data** cable and a stable 5 V USB source. Keep it ventilated during qualification.
2. Explicitly connect it and identify the actual new COM port in Windows. Do not guess from old port numbers or scan every device. Bootloader and application modes may enumerate differently. Verify chip/memory and physical identity before writing.
3. Generate an inert flash plan, replacing `COM5` with the observed port:

   ```powershell
   & .\runtime\python\python.exe -I -B .\scripts\ruview_nodes.py plan --name xiao-three-node --node 1 --port COM5
   ```

   `<ESPTOOL_PYTHON>` in the plan means a separately verified interpreter containing the matching Espressif `esptool`. Core Ariadne Python alone does not imply that optional tool is installed. Use the prepared local flashing-tool receipt to select it; never execute an unresolved placeholder or an unrelated firmware command.
4. Perform USB flashing only after an explicit hardware handoff for the identified board/port and this verified image set. A failed or uncertain physical write needs inspection before another attempt; do not continue automatically across the fleet.
5. Identify the application over USB and provision privately: node ID `1`, the selected local collector IPv4 address/UDP port and authorized 2.4 GHz network details. Supply the password through the hidden local prompt, never chat, command arguments, public Git or a shareable kit. A protocol ACK/reboot confirms configuration, not CSI reception. Stop if identity/configuration differs from expectations.
6. Repeat deliberately for nodes `2` and `3` only after node 1 passes its transport check. Label and record each board's identity before powering all three together.

An `erase_flash`, eFuse/security-setting change, router change or remote OTA is not part of this workflow. No NVS image is written by the four-image flash plan; existing NVS persists.

## Explicit USB onboarding, after flashing

These commands are for the hardware's arrival. Unlike `ruview_nodes.py`, `ruview_onboard.py` can open the chosen physical USB serial port when explicitly armed. It is not exposed as an agent tool, web route or scheduled task. Its optional, matching `pyserial` dependency must be prepared first; core Python alone does not include it.

Extract the matching `ruview-xiao-flash-tools-v1.zip` into `staging/flash-tools`. In a fresh local environment, the pinned wheel cache supports offline installation:

```powershell
$XiaoKitCheck = $null
$XiaoKitCheck = & .\runtime\python\python.exe -I -B .\scripts\ruview_nodes.py status --name xiao-three-node | ConvertFrom-Json -ErrorAction Stop
if ($LASTEXITCODE -ne 0 -or $XiaoKitCheck.status -ne 'completed' -or $XiaoKitCheck.verified -isnot [bool] -or $XiaoKitCheck.verified -ne $true) { throw 'The complete kit and empty esptool configuration must verify before tool installation.' }
if (Test-Path -LiteralPath '.\runtime\ruview-flash') { throw 'The tool environment already exists; inspect its receipt instead of overwriting it.' }
$XiaoPreviousTemp, $XiaoPreviousTmp = $env:TEMP, $env:TMP
$XiaoPreviousEsptoolConfig = $env:ESPTOOL_CFGFILE
$XiaoPreviousPipEnvironment = @{}
Get-ChildItem Env:PIP_* | ForEach-Object { $XiaoPreviousPipEnvironment[$_.Name] = $_.Value }
$XiaoSetupTemp = Join-Path $PWD 'runtime\tmp\ruview-flash'
New-Item -ItemType Directory -Path $XiaoSetupTemp -Force | Out-Null
try {
    Get-ChildItem Env:PIP_* | ForEach-Object { Remove-Item -LiteralPath ('Env:' + $_.Name) }
    $env:PIP_CONFIG_FILE = 'nul'
    $env:PIP_NO_INDEX = '1'
    $env:PIP_NO_INPUT = '1'
    $env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
    $env:TEMP, $env:TMP = $XiaoSetupTemp, $XiaoSetupTemp
    $env:ESPTOOL_CFGFILE = Join-Path $PWD 'data\workspace\ruview-kits\xiao-three-node\esptool.cfg'
    & .\runtime\python\python.exe -I -B -m venv .\runtime\ruview-flash
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment preparation failed; inspect it before another attempt.' }
    & .\runtime\ruview-flash\Scripts\python.exe -I -B -m pip install --no-cache-dir --no-index --no-deps --find-links .\staging\flash-tools\wheels --require-hashes -r .\staging\flash-tools\requirements-flash.txt
    if ($LASTEXITCODE -ne 0) { throw 'Offline tool installation failed; inspect the partial environment.' }
    & .\runtime\ruview-flash\Scripts\python.exe -I -B -m esptool version
    & .\runtime\ruview-flash\Scripts\python.exe -I -B -m pip check
} finally {
    Get-ChildItem Env:PIP_* | ForEach-Object { Remove-Item -LiteralPath ('Env:' + $_.Name) }
    foreach ($XiaoPipName in $XiaoPreviousPipEnvironment.Keys) { Set-Item -LiteralPath ('Env:' + $XiaoPipName) -Value $XiaoPreviousPipEnvironment[$XiaoPipName] }
    $env:TEMP, $env:TMP = $XiaoPreviousTemp, $XiaoPreviousTmp
    $env:ESPTOOL_CFGFILE = $XiaoPreviousEsptoolConfig
}
```

Use a new empty environment; do not overwrite or silently repair a failed/partial installation. Clearing all inherited `PIP_*` settings and selecting lowercase `nul` configuration prevents redirecting installation to an external target/cache/index. The spelling matches Python's Windows `os.devnull`; pip compares the sentinel as a string. The verified kit's empty `esptool.cfg` prevents esptool from discovering inherited or home-directory configuration. Use the complete generated flash plan, including its environment fence and restoration. This installation and the `version` action do not open USB. The wheel pack is qualified for its recorded Windows/Python platform; use its actual receipt and hashes, not an arbitrary similarly named download. This interpreter is also the replacement for `<ESPTOOL_PYTHON>` in an inert flash plan.

Identify the flashed board using its application-mode COM port:

```powershell
& .\runtime\ruview-flash\Scripts\python.exe -I -B .\scripts\ruview_onboard.py identify --name xiao-three-node --node 1 --port COM5 --request-id usb-node-1-id --open-usb
```

Review the returned chip, pilot profile/version, device digest and existing-configuration state. These are device declarations, not cryptographic firmware attestation. Verify physical board identity and the earlier sealed flash independently. An unexpected configured state, changed identity or held attempt needs explicit review before another write.

Supply the observed digest and receiver address locally, then run configuration from a verified environment with the optional serial dependency. This example intentionally asks for the unknown values instead of providing a plausible fake identity or network address:

```powershell
$Node1DeviceDigest = Read-Host 'Paste the 16-character digest from the node 1 identification'
$ReceiverIPv4 = Read-Host 'Private IPv4 address of this collector PC'
& .\runtime\ruview-flash\Scripts\python.exe -I -B .\scripts\ruview_onboard.py configure --name xiao-three-node --node 1 --port COM5 --request-id usb-node-1-config --identification usb-node-1-id --expected-device $Node1DeviceDigest --target-ip $ReceiverIPv4 --target-port 5005 --apply-to-device
```

The client obtains SSID/password through hidden local prompts, checks a fresh bounded nonce/identity exchange and makes only the selected USB configuration request. Neither credential belongs in a command argument or saved kit. Credentials are transmitted locally over USB and deliberately stored in the board's ordinary NVS; this is not encrypted credential storage and enables no eFuse/flash-encryption feature. A device-declared ACK/reboot is not readback of persistent state or a physical RF qualification. Private receipts distinguish those limits.

```powershell
& .\runtime\python\python.exe -I -B .\scripts\ruview_onboard.py status --name xiao-three-node
```

Status only reads saved evidence. An exclusive action reservation prevents concurrent fleet setup. Failed, interrupted, uncertain or conflicting evidence holds new physical actions; there is no automatic retry or failure-clear action. Node 2 and node 3 require their own fresh identification, observed COM port/digest and request IDs; never reuse node 1's identity declarations for them.

## Physical acceptance and recording handoff

- **USB and boot:** verify S3 identity, expected flash/PSRAM, firmware identity and native USB onboarding. Check headless/LED/WASM settings and that a new unconfigured board does not join a network before provisioning.
- **One real transport:** explicitly start a reviewed collector with the real ESP32 source selected and simulation fallback disabled. Measure received CSI rate, missing packets, outages and age. AP ping success and UDP `sendto()` success do not prove reception at the PC. RuView's hardware contract uses a 20-packet/second CSI acceptance floor; it is not an accuracy score or a guarantee.
- **Three identities:** require distinct expected node IDs and physical identities. Reject duplicates, unexpected source families and stale nodes. A server update counter is not a radio sequence or physical observation clock.
- **Room tests:** freeze placement and collect labeled empty/occupied, still/moving, adjacent-room/interference and disconnected-node trials. Hold out different sessions for evaluation. Record misses, false positives, gaps and delays. Three links do not by themselves validate pose, localization, people counts, medical interpretation or radar anywhere.

The firmware kit does not install/start a RuView host server. Its host-side dependencies need their own reviewed preparation; do not substitute an arbitrary executable or automatic `--source auto` fallback. Keep Ariadne on `127.0.0.1:8080` and choose a nonconflicting loopback HTTP port for RuView, for example `8098`. LAN UDP collection needs an explicit bind address/port and private source allowlist. This document changes no listener, firewall, router or service.

After transport is verified, save a bounded native JSONL recording and use [Ariadne's recorded RuView workflow](RUVIEW_WIFI.md). Its 1,000,000-byte/512-observation bounds remain unchanged. Preserve source identity, unknown radio clocks and simulation labels. Import/replay never establishes current occupancy or grants hardware/workflow authority.

## Privacy and cost

Public source, credential-free firmware and checksums can be shared. Keep owner receipts, serial identities, configuration, logs and RF recordings private. Hashes establish matching bytes, not authentic hardware or true inference. Preparation/verification/replay need no model API, subscription or recurring poller. SDK downloads are development preparation; future continuous radios, collection, storage and maintenance have operating costs not yet measured on these boards.

Primary references: [RuView firmware/headless notes](https://github.com/ruvnet/RuView/blob/094e1400719ded1283c67d23b9c737f6738a3244/firmware/esp32-csi-node/README.md), [Seeed board/antenna/USB guide](https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/), [ESP-IDF 5.4 Windows build guide](https://docs.espressif.com/projects/esp-idf/en/v5.4/esp32s3/get-started/windows-setup.html).
