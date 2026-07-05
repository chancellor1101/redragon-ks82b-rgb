"""Control-socket transport shared by the daemon (server) and clients.

Protocol: one JSON object per line over a Unix domain socket.  Request
``{"cmd": "...", ...}`` -> response ``{"ok": true, ...}`` or
``{"ok": false, "error": "..."}``.
"""

import json
import os
import socket


def socket_path():
    """Per-user socket under XDG_RUNTIME_DIR (falls back to /tmp)."""
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(base, f"ks82rgb-{os.getuid()}.sock")


# ------------------------------------------------------------------ client ----
def request(req, timeout=2.0):
    """Send one request to the daemon; return the response dict.

    Returns None if the daemon isn't running (socket missing / refused), so
    callers can fall back to direct device access.
    """
    path = socket_path()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
    except OSError:
        s.close()
        return None
    try:
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        if not buf:
            return None
        return json.loads(buf.split(b"\n", 1)[0].decode())
    except (OSError, ValueError):
        return None
    finally:
        s.close()


def daemon_running():
    r = request({"cmd": "ping"}, timeout=1.0)
    return bool(r and r.get("ok"))


# ------------------------------------------------------------------ server ----
def make_server():
    """Create + bind the listening Unix socket, replacing any stale one."""
    path = socket_path()
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    os.chmod(path, 0o600)
    srv.listen(8)
    return srv, path


def serve_forever(srv, handler, should_stop):
    """Accept connections and dispatch each line to `handler(dict) -> dict`.

    `should_stop()` is polled so the loop exits promptly on shutdown.
    """
    srv.settimeout(0.5)
    while not should_stop():
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        with conn:
            try:
                buf = b""
                while b"\n" not in buf:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                if not buf:
                    continue
                for line in buf.split(b"\n"):
                    if not line.strip():
                        continue
                    try:
                        req = json.loads(line.decode())
                        resp = handler(req)
                    except Exception as e:
                        resp = {"ok": False, "error": str(e)}
                    conn.sendall((json.dumps(resp) + "\n").encode())
                    break  # one request per connection
            except OSError:
                pass
