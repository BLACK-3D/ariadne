"""Fixed local video adapter: sealed FFmpeg FD decoder and OpenCV CPU YOLOX.

There are no arbitrary commands, URLs, model downloads, microphones, numeric
camera probes, or capture recovery paths in this executable.
"""
from __future__ import annotations

import base64
from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime" / "vision"
SIZE = 416
WIDTH, HEIGHT = 640, 480
FRAME_BYTES = WIDTH * HEIGHT * 3
MAX_SAMPLES = 241
MAX_DECODED_FRAMES = 14_400
MAX_DURATION_MS = 120_000
LABELS = ("person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush")
_PTS = re.compile(r"\bn:\s*(\d+)\s+pts:\s*(-?\d+)\s+pts_time:\s*(-?[0-9.eE+]+)")
_DIM = re.compile(r"\bs:(\d+)x(\d+)\b")
_VIDEO = re.compile(r'"(.{1,160})"\s+\(video\)')
_ALT = re.compile(r'Alternative name\s+"(.{1,1000})"')


def stamp():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def emit(value):
    raw = (json.dumps(value, allow_nan=False, separators=(",", ":")) + "\n").encode()
    if len(raw) > 128_000:
        raise ValueError("Vision response line exceeds its bound.")
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def deny_network(event, _args):
    if event.startswith("socket.") or event in {"urllib.Request", "http.client.connect", "os.system", "os.posix_spawn", "os.exec", "os.spawn"}:
        raise PermissionError("Vision worker network and general command execution are disabled.")


def decoder_path():
    from ariadne.vision import vision_assets
    assets = vision_assets(verify=True)
    if not assets["ready"]:
        raise ValueError(assets["reason"])
    return str(RUNTIME / assets["manifest"]["decoder"]["path"])


def _popen(command, **kwargs):
    # Every argument originates in one of the fixed builders in this module.
    if command[0] != str(RUNTIME / "ffmpeg" / "ffmpeg.exe"):
        raise ValueError("Unapproved vision decoder executable.")
    return subprocess.Popen(command, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **kwargs)


def camera_inventory(executable):
    if os.name != "nt":
        return []
    process = _popen([executable, "-hide_banner", "-nostdin", "-list_devices", "true", "-f", "dshow", "-i", "dummy"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    lines, overflow = [], threading.Event()
    def read():
        total = 0
        while True:
            row = process.stderr.readline(4097)
            if not row:
                break
            total += len(row)
            if total > 64_000 or len(row) > 4096:
                overflow.set()
                process.kill()
                break
            lines.append(row.decode("utf-8", "replace"))
    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    try:
        process.wait(timeout=6)
        thread.join(timeout=1)
        if thread.is_alive() or overflow.is_set():
            raise ValueError("Camera inventory exceeded its finite output bound.")
        devices, pending = [], None
        for row in lines:
            video = _VIDEO.search(row)
            if video:
                pending = video.group(1)
                continue
            if "(audio)" in row:
                pending = None
            alternate = _ALT.search(row)
            if pending and alternate:
                identity = alternate.group(1)
                # Alternative names are adapter-generated monikers, never URLs
                # or arbitrary owner-supplied FFmpeg input expressions.
                if any(ord(char) < 32 for char in identity):
                    raise ValueError("Camera has an invalid DirectShow alternative name.")
                if not identity.startswith("@device_pnp_") or "usb#" not in identity.lower():
                    pending = None
                    continue  # Virtual/unsupported adapters are never probed.
                devices.append({"device_id": "usb-" + hashlib.sha256(identity.encode()).hexdigest()[:40], "label": pending,
                                "backend": "directshow_alternative_name", "available": True, "_alternative_name": identity})
                pending = None
        if len(devices) > 16 or len({device["device_id"] for device in devices}) != len(devices):
            raise ValueError("Camera inventory exceeds its device bound or repeats an identity.")
        return devices
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=2)
        process.stderr.close()


class Detector:
    def __init__(self):
        sys.path.insert(0, str(RUNTIME / "python"))
        import cv2
        import numpy as np
        for module in (cv2, np):
            if not Path(module.__file__).resolve().is_relative_to((RUNTIME / "python").resolve()):
                raise ValueError("Vision imported code outside its sealed optional runtime.")
        if cv2.__version__ != "5.0.0" or np.__version__ != "2.5.4":
            raise ValueError("Unexpected vision runtime version.")
        cv2.setNumThreads(1)
        cv2.ocl.setUseOpenCL(False)
        self.cv, self.np = cv2, np
        self.net = cv2.dnn.readNetFromONNX(str(RUNTIME / "models" / "yolox_nano.onnx"))
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        grids, strides = [], []
        for stride in (8, 16, 32):
            height = width = SIZE // stride
            x, y = np.meshgrid(np.arange(width), np.arange(height))
            grids.append(np.stack((x, y), axis=2).reshape(-1, 2))
            strides.append(np.full((height * width, 1), stride))
        self.grid, self.strides = np.concatenate(grids).astype(np.float32), np.concatenate(strides).astype(np.float32)
        self.previous = None
        self.previous_hash = None

    def infer(self, frame):
        cv, np = self.cv, self.np
        start = time.monotonic()
        height, width = frame.shape[:2]
        ratio = min(SIZE / height, SIZE / width)
        resized = cv.resize(frame, (int(width * ratio), int(height * ratio)), interpolation=cv.INTER_LINEAR)
        padded = np.full((SIZE, SIZE, 3), 114, dtype=np.uint8)
        padded[:resized.shape[0], :resized.shape[1]] = resized
        # Official nonlegacy YOLOX input: BGR, 0..255, CHW float32,
        # top-left letterbox. No RGB swap or normalization.
        blob = np.ascontiguousarray(padded.transpose(2, 0, 1)[None], dtype=np.float32)
        input_hash = hashlib.sha256(blob.tobytes()).hexdigest()
        self.net.setInput(blob)
        output = np.asarray(self.net.forward())
        if output.shape != (1, 3549, 85) or not np.isfinite(output).all():
            raise ValueError("YOLOX output has an unexpected shape or nonfinite values.")
        prediction = output[0].copy()
        prediction[:, :2] = (prediction[:, :2] + self.grid) * self.strides
        prediction[:, 2:4] = np.exp(np.clip(prediction[:, 2:4], -20, 20)) * self.strides
        scores = prediction[:, 4:5] * prediction[:, 5:]
        classes = scores.argmax(axis=1)
        confidence = scores[np.arange(len(scores)), classes]
        detections = []
        for class_id in range(80):
            selected = np.flatnonzero((classes == class_id) & (confidence >= .35))
            if not len(selected):
                continue
            if len(selected) > 1000:
                selected = selected[np.argsort(confidence[selected])[-1000:]]
            boxes = prediction[selected, :4].copy()
            boxes[:, :2] -= boxes[:, 2:4] / 2
            boxes /= ratio
            keep = cv.dnn.NMSBoxes(boxes.tolist(), confidence[selected].tolist(), .35, .45, top_k=32)
            for kept in np.asarray(keep).reshape(-1):
                index = selected[int(kept)]
                x, y, box_width, box_height = boxes[int(kept)]
                left, top = max(0., float(x)), max(0., float(y))
                right, bottom = min(float(width), float(x + box_width)), min(float(height), float(y + box_height))
                if right <= left or bottom <= top:
                    continue
                detections.append({"label": LABELS[class_id], "score": round(float(confidence[index]), 6),
                                   "bbox_normalized": [round(left / width, 6), round(top / height, 6), round((right-left) / width, 6), round((bottom-top) / height, 6)]})
        detections = sorted(detections, key=lambda row: (-row["score"], row["label"], row["bbox_normalized"]))[:32]
        gray = cv.cvtColor(cv.resize(frame, (160, 120), interpolation=cv.INTER_AREA), cv.COLOR_BGR2GRAY)
        difference = None if self.previous is None else float(cv.absdiff(gray, self.previous).mean())
        frame_hash = hashlib.sha256(frame.tobytes()).hexdigest()
        motion = {"state": "unknown" if difference is None else "detected" if difference >= 8.0 else "not_detected",
                  "method": "grayscale_mean_absolute_difference", "mean_absolute_difference": round(difference, 6) if difference is not None else None,
                  "threshold": 8.0, "previous_frame_sha256": self.previous_hash}
        self.previous, self.previous_hash = gray, frame_hash
        return detections, motion, frame_hash, input_hash, round((time.monotonic() - start) * 1000, 3)


def container_header(source):
    header = source.read(32)
    source.seek(0)
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "mov"
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        return "matroska"
    if header.startswith(b"RIFF") and header[8:12] == b"AVI ":
        return "avi"
    raise ValueError("Video header is not an accepted MP4/MOV, Matroska/WebM or AVI container.")


def _open_snapshot(path, registry, directory):
    """Deny writers on Windows; hash and decode exactly this held descriptor."""
    if os.name != "nt":
        return registry._open_read(path, directory)
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    handle = create(str(path), 0x80000000, 1, None, 3, 0x08000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        final = kernel.GetFinalPathNameByHandleW
        final.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        final.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        count = final(handle, buffer, len(buffer), 0)
        if not count or count >= len(buffer) or os.path.normcase(buffer.value.removeprefix("\\\\?\\")) != os.path.normcase(str(path.absolute())):
            raise ValueError("Video snapshot path changed while opening its immutable handle.")
        registry._check_ancestors(path)
        descriptor = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
        handle = None
        return descriptor
    finally:
        if handle is not None:
            close(handle)


def decode(request, executable, detector, stop_event):
    if request["operation"] != "video":
        return _decode(request, executable, detector, stop_event)
    from ariadne.tools import ToolRegistry
    from ariadne.config import _allowed_path
    from ariadne.vision import MAX_VIDEO_BYTES, _identity
    import stat
    path = _allowed_path(Path(request["source_path"]))
    if path.name not in {"input.mp4", "input.mov", "input.mkv", "input.webm", "input.avi"}:
        raise ValueError("Invalid fixed video snapshot name.")
    registry = object.__new__(ToolRegistry)
    with ExitStack() as stack:
        directory = stack.enter_context(registry._directory(path.parent))
        # FFmpeg inherits the native offset, not Python's buffered position.
        # Unbuffered reads ensure header/hash rewind really rewinds that handle.
        source = stack.enter_context(os.fdopen(_open_snapshot(path, registry, directory), "rb", buffering=0))
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= MAX_VIDEO_BYTES:
            raise ValueError("Video snapshot is not a bounded unlinked regular file.")
        digest, size = hashlib.sha256(), 0
        for block in iter(lambda: source.read(1024**2), b""):
            size += len(block)
            if size > MAX_VIDEO_BYTES:
                raise ValueError("Video snapshot grew beyond its bound.")
            digest.update(block)
        if _identity(before) != _identity(os.fstat(source.fileno())) or digest.hexdigest() != request["source_sha256"]:
            raise ValueError("Video snapshot integrity check failed.")
        source.seek(0)
        container_header(source)
        result = _decode(request, executable, detector, stop_event, source=source)
        if _identity(before) != _identity(os.fstat(source.fileno())):
            raise ValueError("Video snapshot changed during native decoding.")
        return result


def _decode(request, executable, detector, stop_event, *, source=None):
    operation = request["operation"]
    interval = request["sample_interval_ms"] / 1000
    selection = "select=isnan(prev_selected_t)+gte(t-prev_selected_t\\," + str(interval) + ")"
    # showinfo precedes scaling: retained PTS and dimensions belong to the
    # original decoded frame. Output is fixed-size BGR for a bounded pipe.
    filters = "showinfo@source," + selection + ",showinfo@selected,scale=640:480:force_original_aspect_ratio=decrease,pad=640:480:(ow-iw)/2:(oh-ih)/2"
    command = [executable, "-hide_banner", "-nostdin", "-loglevel", "info", "-copyts", "-max_alloc", "67108864", "-threads", "1", "-filter_threads", "1"]
    if operation == "video":
        # FD input is a seekable regular file, including MP4 with tail moov.
        # Native protocol restriction denies file, network, concat and devices.
        command += ["-protocol_whitelist", "fd,pipe", "-format_whitelist", "mov,matroska,avi", "-t", str(request["duration_ms"] / 1000), "-fd", "0", "-i", "fd:"]
    else:
        if os.name != "nt":
            raise ValueError("This camera adapter supports Windows USB devices only.")
        devices = camera_inventory(executable)
        device = next((row for row in devices if row["device_id"] == request["device_id"]), None)
        if device is None:
            raise ValueError("Selected stable camera identity is no longer present.")
        command += ["-rtbufsize", "8M", "-f", "dshow", "-t", str(request["duration_ms"] / 1000), "-i", "video=" + device["_alternative_name"]]
    command += ["-map", "0:v:0", "-an", "-sn", "-dn", "-vf", filters, "-fps_mode", "passthrough", "-pix_fmt", "bgr24", "-c:v", "rawvideo", "-threads", "1", "-f", "rawvideo", "pipe:1"]
    begin = time.monotonic()
    process = _popen(command, stdin=source if source else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    images, timestamps = queue.Queue(maxsize=2), queue.Queue(maxsize=4)
    failures = []
    finished = threading.Event()
    decoded_count = [0]
    source_index_by_pts = {}
    def put(target, value):
        while not finished.is_set():
            try:
                target.put(value, timeout=.1)
                return
            except queue.Full:
                pass
    def read_images():
        try:
            while not finished.is_set():
                raw = bytearray()
                while len(raw) < FRAME_BYTES:
                    block = process.stdout.read(FRAME_BYTES - len(raw))
                    if not block:
                        break
                    raw.extend(block)
                if not raw:
                    break
                if len(raw) != FRAME_BYTES:
                    raise ValueError("Video decoder returned a partial raw frame.")
                put(images, (bytes(raw), stamp(), time.monotonic()))
        except (OSError, ValueError) as error:
            failures.append(error)
        finally:
            put(images, None)
    def read_timestamps():
        try:
            lines, count = 0, 0
            while not finished.is_set():
                raw = process.stderr.readline(8193)
                if not raw:
                    break
                lines += len(raw)
                if len(raw) > 8192 or lines > 16 * 1024**2:
                    raise ValueError("Video decoder diagnostics exceeded their bound.")
                row = raw.decode("utf-8", "replace")
                match, dimensions = _PTS.search(row), _DIM.search(row)
                if match and dimensions:
                    if "showinfo@source" in row:
                        decoded_count[0] += 1
                        if decoded_count[0] > MAX_DECODED_FRAMES:
                            raise ValueError("Video exceeds its decoded-frame bound.")
                        source_index_by_pts[match.group(2)] = int(match.group(1))
                        if len(source_index_by_pts) > 8:
                            source_index_by_pts.pop(next(iter(source_index_by_pts)))
                        width, height = int(dimensions.group(1)), int(dimensions.group(2))
                        if not 1 <= width <= 1920 or not 1 <= height <= 1080:
                            raise ValueError("Original video dimensions exceed the admitted bounds.")
                        continue
                    if "showinfo@selected" not in row:
                        continue
                    count += 1
                    if count > MAX_SAMPLES:
                        raise ValueError("Video exceeds the duration or sample bound.")
                    pts = float(match.group(3)) * 1000
                    width, height = int(dimensions.group(1)), int(dimensions.group(2))
                    maximum_pts = 2**53 - 1
                    if not math.isfinite(pts) or abs(pts) > maximum_pts or not 1 <= width <= 1920 or not 1 <= height <= 1080:
                        raise ValueError("Original video dimensions or timestamp exceed the admitted bounds.")
                    if match.group(2) not in source_index_by_pts:
                        raise ValueError("Sample lacks its decoded source-frame index.")
                    put(timestamps, (source_index_by_pts[match.group(2)], pts, width, height))
        except (ValueError, OSError) as error:
            failures.append(error)
            process.kill()
    readers = [threading.Thread(target=read_images, daemon=True), threading.Thread(target=read_timestamps, daemon=True)]
    for thread in readers:
        thread.start()
    count, latest, first_pts, last_pts = 0, None, None, None
    try:
        while True:
            if stop_event.is_set():
                break
            limit = request["duration_ms"] / 1000 + 25 if operation == "camera" else 175
            if time.monotonic() - begin > limit:
                raise ValueError("Video decoding exceeded its finite wall-time bound.")
            if failures:
                raise failures[0]
            try:
                item = images.get(timeout=.1)
            except queue.Empty:
                continue
            if item is None:
                break
            raw, received_at, received_monotonic = item
            try:
                index, pts, original_width, original_height = timestamps.get(timeout=3)
            except queue.Empty:
                raise ValueError("Decoded frame lacks an original timestamp.") from None
            if first_pts is None:
                first_pts = pts
            if last_pts is not None and pts < last_pts:
                raise ValueError("Original video timestamps regress.")
            last_pts = pts
            offset = max(0, round(pts - first_pts))
            if offset > request["duration_ms"] or count >= MAX_SAMPLES:
                raise ValueError("Video duration exceeds its explicit finite limit.")
            frame = detector.np.frombuffer(raw, dtype=detector.np.uint8).reshape(HEIGHT, WIDTH, 3)
            detections, motion, frame_hash, input_hash, inference_ms = detector.infer(frame)
            capture_elapsed = max(0, round((received_monotonic - begin) * 1000)) if operation == "camera" else None
            source_lag = max(0, capture_elapsed - offset) if capture_elapsed is not None else None
            row = {"sample_index": count, "decoded_frame_index": index, "source_pts_ms": round(pts, 6),
                   "timeline_offset_ms": offset, "timestamp_basis": "container_pts" if operation == "video" else "camera_source_pts",
                   "source_lag_ms": source_lag, "capture_elapsed_ms": capture_elapsed,
                   "acquired_at": received_at if operation == "camera" else None, "width": WIDTH, "height": HEIGHT, "source_width": original_width, "source_height": original_height,
                   "frame_sha256": frame_hash, "inference_input_sha256": input_hash, "detections": detections, "motion": motion, "inference_ms": inference_ms}
            emit({"type": "frame", "frame": row})
            count += 1
            latest = frame.copy() if request["thumbnail"] else None
        if failures:
            raise failures[0]
        if not stop_event.is_set():
            process.wait(timeout=3)
            if process.returncode:
                raise ValueError("Video decoder rejected this file or camera format.")
        thumbnail = None
        if latest is not None:
            small = detector.cv.resize(latest, (320, 240), interpolation=detector.cv.INTER_AREA)
            good, encoded = detector.cv.imencode(".jpg", small, [detector.cv.IMWRITE_JPEG_QUALITY, 65])
            if not good or len(encoded) > 120_000:
                raise ValueError("Latest thumbnail exceeds its byte bound.")
            thumbnail = base64.b64encode(encoded).decode("ascii")
        return {"type": "result", "decoder": "sealed-ffmpeg-fd-rawvideo", "decoded_frames": decoded_count[0],
                "duration_ms": max(0, round((last_pts or 0) - (first_pts or 0))), "thumbnail": thumbnail}
    finally:
        finished.set()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
        for stream in (process.stdout, process.stderr):
            stream.close()
        for thread in readers:
            thread.join(timeout=1)


def main():
    sys.path.insert(0, str(ROOT))
    sys.addaudithook(deny_network)
    try:
        # Use the raw descriptor for the blocking control reader. A daemon
        # holding BufferedReader's lock can deadlock Python finalization while
        # the parent deliberately keeps its control pipe open.
        raw = bytearray()
        while len(raw) <= 32_768 and not raw.endswith(b"\n"):
            char = os.read(sys.stdin.fileno(), 1)
            if not char:
                break
            raw.extend(char)
        raw = bytes(raw)
        if not raw.endswith(b"\n") or len(raw) > 32_768:
            raise ValueError("Invalid vision request header.")
        request = json.loads(raw)
        if type(request) is not dict or request.get("operation") not in {"discover", "video", "camera"}:
            raise ValueError("Invalid fixed vision operation.")
        executable = decoder_path()
        if request["operation"] == "discover":
            devices = [{key: value for key, value in row.items() if key != "_alternative_name"} for row in camera_inventory(executable)]
            emit({"type": "result", "devices": devices})
            return
        expected = {"operation", "duration_ms", "sample_interval_ms", "thumbnail"} | ({"source_path", "source_sha256"} if request["operation"] == "video" else {"device_id"})
        if set(request) != expected or type(request["duration_ms"]) is not int or not 1000 <= request["duration_ms"] <= MAX_DURATION_MS or type(request["sample_interval_ms"]) is not int or not 500 <= request["sample_interval_ms"] <= 10000 or type(request["thumbnail"]) is not bool:
            raise ValueError("Invalid bounded vision request.")
        # NumPy/OpenBLAS initialization on Windows can query CRT stdin state.
        # A simultaneous blocking CRT read of fd 0 deadlocks that DLL import.
        # Load the sealed detector first; no camera is opened during this step.
        detector = Detector()
        stop_event = threading.Event()
        control_ready = threading.Event()
        control_reason = {"value": None}
        lease_deadline = [time.monotonic() + 5]
        def read_control():
            try:
                pending = bytearray()
                while not stop_event.is_set():
                    chunk = os.read(sys.stdin.fileno(), 64)
                    if not chunk:
                        control_reason["value"] = "parent_disconnected"
                        stop_event.set()
                        control_ready.set()
                        break
                    pending.extend(chunk)
                    while b"\n" in pending:
                        control, _, rest = pending.partition(b"\n")
                        pending = bytearray(rest)
                        if control == b'{"lease":true}' and request["operation"] == "camera" and time.monotonic() <= lease_deadline[0]:
                            lease_deadline[0] = time.monotonic() + 5
                            control_ready.set()
                        else:
                            control_reason["value"] = "owner_stop" if control == b'{"stop":true}' else "invalid_control"
                            stop_event.set()
                            control_ready.set()
                    if len(pending) > 64:
                        control_reason["value"] = "invalid_control"
                        stop_event.set()
            except (OSError, ValueError):
                control_reason["value"] = "parent_disconnected"
                stop_event.set()
                control_ready.set()
        if request["operation"] == "camera":
            threading.Thread(target=read_control, daemon=True).start()
            def watch_lease():
                while not stop_event.wait(.1):
                    if time.monotonic() > lease_deadline[0]:
                        control_reason["value"] = "worker_lease_expired"
                        stop_event.set()
            threading.Thread(target=watch_lease, daemon=True).start()
            if not control_ready.wait(timeout=2):
                control_reason["value"] = "worker_lease_timeout"
                stop_event.set()
        if stop_event.is_set():
            emit({"type": "result", "decoder": "sealed-ffmpeg-fd-rawvideo", "decoded_frames": 0, "duration_ms": 0, "thumbnail": None, "stop_reason": control_reason["value"]})
            return
        result = decode(request, executable, detector, stop_event)
        result["stop_reason"] = control_reason["value"] if stop_event.is_set() else None
        emit(result)
    except BaseException as error:
        emit({"type": "result", "error": (str(error) or type(error).__name__)[:300]})


if __name__ == "__main__":
    if sys.argv[1:]:
        raise SystemExit(2)
    main()
