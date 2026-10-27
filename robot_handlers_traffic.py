"""Robot iperf3 client execution. NMS integration is a separate patch.

Script reverse=True continues to mean robot upload. The iperf3 -R flag
therefore has the opposite value. Raw iperf3 JSON is never rewritten.
Pending records are durable; an HTTP success alone must not delete them.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
import uuid

from config import TRAFFIC_STATE_DIR, TRAFFIC_IPERF3_BIN, get_ax210_iface, local_ts
from robot_status_report import get_wifi_status

AC_TO_TOS = {"vo": 184, "vi": 136, "be": 0, "bk": 32}
_LOCK = threading.RLock()
_RUNNING = {}


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _record(kind, payload):
    record_id = uuid.uuid4().hex
    record = {"record_id": record_id, "kind": kind, **payload}
    _write(Path(TRAFFIC_STATE_DIR) / "pending" / (record_id + ".json"), record)
    return record


def pending_records(limit=20):
    """For the next integration patch: read without consuming records."""
    records = []
    with _LOCK:
        for path in sorted((Path(TRAFFIC_STATE_DIR) / "pending").glob("*.json")):
            records.append(json.loads(path.read_text(encoding="utf-8")))
            if len(records) >= limit:
                break
    return records


def acknowledge_records(record_ids):
    """Call only for individual IDs durably accepted by the NMS."""
    with _LOCK:
        for record_id in record_ids:
            if re.fullmatch(r"[0-9a-f]{32}", str(record_id)):
                (Path(TRAFFIC_STATE_DIR) / "pending" / (record_id + ".json")).unlink(missing_ok=True)


def quarantine_records(rejections):
    """Keep rejected records on disk for inspection instead of dropping them."""
    with _LOCK:
        for item in rejections:
            record_id = item.get("record_id")
            if not isinstance(record_id, str) or not re.fullmatch(r"[0-9a-f]{32}", record_id):
                continue
            source = Path(TRAFFIC_STATE_DIR) / "pending" / (record_id + ".json")
            if source.exists():
                record = json.loads(source.read_text(encoding="utf-8"))
                record["rejection_detail"] = str(item.get("detail") or "")
                _write(Path(TRAFFIC_STATE_DIR) / "rejected" / source.name, record)
                source.unlink()


def _integer(value, name, low, high):
    if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value)):
        raise ValueError(name + " must be an integer")
    value = int(value)
    if not low <= value <= high:
        raise ValueError(name + " out of range")
    return value


def _prepare(args):
    # server_host/server_port are internal delivery fields, not new script fields.
    host = str(args.get("server_host") or "").strip()
    if not host or host.startswith("-") or any(c.isspace() for c in host):
        raise ValueError("server_host required")
    port = _integer(args.get("server_port"), "server_port", 1, 65535)
    duration = _integer(args.get("duration_sec"), "duration_sec", 1, 1200)
    interval = _integer(args.get("report_interval_sec", 60), "report_interval_sec", 1, 60)
    reverse = args.get("reverse")
    if not isinstance(reverse, bool):
        raise ValueError("reverse must be a boolean")
    ac = str(args.get("ac") or "").lower()
    protocol = str(args.get("protocol") or "").lower()
    if ac not in AC_TO_TOS or protocol not in ("tcp", "udp"):
        raise ValueError("invalid ac or protocol")
    iface = get_ax210_iface()
    if not iface:
        raise ValueError("AX210 interface unavailable")
    wifi = get_wifi_status(iface)
    if not wifi.get("connected"):
        raise ValueError("AX210 is not connected")
    addresses = json.loads(subprocess.check_output(
        ["ip", "-j", "-4", "addr", "show", "dev", iface], text=True, timeout=2))
    ips = [a["local"] for entry in addresses for a in entry.get("addr_info", [])
           if a.get("family") == "inet" and a.get("scope") == "global"]
    if not ips:
        raise ValueError("AX210 has no IPv4 address")
    cmd = [TRAFFIC_IPERF3_BIN, "-4", "-c", host, "-p", str(port),
           "-B", ips[0], "-t", str(duration), "-i", str(interval),
           "--tos", str(AC_TO_TOS[ac]), "-J"]
    if not reverse:
        cmd.append("-R")
    metadata = {"reverse": reverse, "protocol": protocol, "ac": ac,
                "duration_sec": duration, "report_interval_sec": interval,
                "server_host": host, "server_port": port,
                "client_role": "robot", "iperf_reverse": not reverse,
                "wifi_start": wifi, "source_ip": ips[0]}
    if protocol == "udp":
        bitrate = str(args.get("bitrate") or "")
        if not re.fullmatch(r"[1-9]\d*(?:\.\d+)?[KMG]?", bitrate):
            raise ValueError("invalid bitrate")
        size = _integer(args.get("packet_size"), "packet_size", 64, 1472)
        cmd += ["-u", "-b", bitrate, "-l", str(size)]
        metadata.update(bitrate=bitrate, packet_size=size)
    else:
        parallel = _integer(args.get("parallel", 1), "parallel", 1, 16)
        cmd += ["-P", str(parallel)]
        metadata["parallel"] = parallel
    return cmd, metadata


def start_session(scanner, args, execution_id):
    """Return immediately after process launch; command ID prevents replay."""
    session_id = str(args.get("session_id") or "").strip()
    if not scanner or not session_id or not execution_id:
        return False, "scanner, session_id and cmd_id required"
    key = (scanner, session_id)
    digest = hashlib.sha256((scanner + ":" + str(execution_id)).encode()).hexdigest()
    claim = Path(TRAFFIC_STATE_DIR) / "executions" / (digest + ".json")
    with _LOCK:
        if claim.exists():
            previous = json.loads(claim.read_text(encoding="utf-8"))
            return previous.get("status") != "error", "execution already recorded: " + previous.get("status", "unknown")
        if key in _RUNNING:
            return False, "session already running"
        try:
            cmd, metadata = _prepare(args)
            ports = args.get("server_ports") or [args["server_port"]]
            if not isinstance(ports, list) or not 1 <= len(ports) <= 100:
                raise ValueError("invalid server_ports")
            ports = [_integer(port, "server_port", 1, 65535) for port in ports]
            first_port = int(args["server_port"])
            ports = [first_port] + [port for port in ports if port != first_port]
            context = {"scanner": scanner, "session_id": session_id,
                       "execution_id": str(execution_id), **metadata}
            # Fail before launch if durable state cannot be written.
            _write(claim, {"status": "launching", **context})
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, start_new_session=True)
        except Exception as exc:
            detail = "start failed: " + str(exc)
            _write(claim, {"status": "error", "detail": detail})
            _record("result", {"scanner": scanner, "session_id": session_id,
                               "execution_id": str(execution_id), "status": "error",
                               "detail": detail, "completion_time": local_ts(), "raw": {}})
            return False, detail
        started_at = local_ts()
        state = {"proc": proc, "stop_requested": False, "execution_id": str(execution_id)}
        _RUNNING[key] = state

        def watch():
            nonlocal proc, context, started_at
            raw, detail, status = {}, "", "ok"
            attempts = []
            deadline = time.monotonic() + metadata["duration_sec"] + 45
            try:
                for index, port in enumerate(ports):
                    if index:
                        with _LOCK:
                            if state["stop_requested"]:
                                break
                            next_args = {**args, "server_port": port}
                            next_cmd, next_meta = _prepare(next_args)
                            proc = subprocess.Popen(next_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                    text=True, start_new_session=True)
                            state["proc"] = proc
                            context.update(next_meta)
                            started_at = local_ts()
                    try:
                        out, err = proc.communicate(timeout=max(.1, deadline - time.monotonic()))
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        out, err = proc.communicate()
                        status, detail = "error", "session timeout"
                    try:
                        raw = json.loads(out)
                        if not isinstance(raw, dict):
                            raise ValueError("expected JSON object")
                    except (ValueError, TypeError):
                        raw = {"raw_output": out}
                        status, detail = "error", detail or "invalid iperf3 JSON"
                    attempts.append({"server_port": port, "error": str(raw.get("error") or ""),
                                     "returncode": proc.returncode})
                    busy = "server is busy" in str(raw.get("error") or "").lower()
                    if busy and index + 1 < len(ports) and not state["stop_requested"] and time.monotonic() < deadline:
                        continue
                    if proc.returncode != 0 or raw.get("error"):
                        status = "error"
                        detail = detail or str(raw.get("error") or err.strip() or "iperf3 failed")
                    break
                if state["stop_requested"]:
                    status, detail = "stopped", "stop requested"
            except Exception as exc:
                status, detail = "error", "watcher failed: " + str(exc)
            finally:
                with _LOCK:
                    try:
                        try:
                            wifi_end = get_wifi_status(metadata["wifi_start"]["iface"])
                        except Exception:
                            wifi_end = {}
                        _record("result", {**context, "started_at": started_at,
                                           "completion_time": local_ts(), "status": status,
                                           "detail": detail, "raw": raw, "wifi_end": wifi_end,
                                           "attempts": attempts, "returncode": proc.returncode})
                        _write(claim, {**context, "status": status, "detail": detail})
                    finally:
                        _RUNNING.pop(key, None)

        threading.Thread(target=watch, daemon=True).start()
        _record("event", {**context, "action": "traffic.session.start",
                          "status": "ok", "started_at": started_at,
                          "completion_time": started_at, "detail": "client process launched"})
        return True, "client process launched: " + session_id


def stop_session(scanner, args):
    key = (scanner, str(args.get("session_id") or "").strip())
    with _LOCK:
        state = _RUNNING.get(key)
        if state is None:
            return False, "no running session"
        expected = str(args.get("execution_id") or "")
        if expected and expected != state["execution_id"]:
            return False, "stop execution does not match running session"
        state["stop_requested"] = True
        try:
            os.killpg(state["proc"].pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as exc:
            state["stop_requested"] = False
            return False, "stop failed: " + str(exc)
        proc = state["proc"]
        def kill_if_needed():
            with _LOCK:
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        timer = threading.Timer(3, kill_if_needed)
        timer.daemon = True
        timer.start()
        _record("event", {"scanner": scanner, "session_id": key[1],
                          "execution_id": state["execution_id"],
                          "action": "traffic.session.stop", "status": "ok",
                          "detail": "stop requested", "completion_time": local_ts()})
        return True, "stop requested"
