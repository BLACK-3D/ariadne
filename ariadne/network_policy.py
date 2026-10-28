"""Process-local protection against accidental Python network access.

Install early, before constructing application clients. This is not an OS
sandbox: native libraries/processes and already connected sockets need their
own offline controls. No DNS lookup is performed by the policy itself.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import sys
import threading


class OfflineNetworkError(PermissionError):
    """An outbound operation was refused by Ariadne's offline policy."""


_lock = threading.Lock()
_lookup_context = threading.local()
_installed = False
_enabled = False
_CAVEATS = (
    "Python process only; not an OS firewall or security sandbox.",
    "Native libraries, native subprocesses, direct low-level C socket calls, "
    "and sockets connected before installation require separate offline controls.",
    "Incoming LAN connections and replies on accepted connections remain allowed.",
    "Changing ARIADNE_OFFLINE requires restarting this process.",
)


def _offline_setting() -> bool:
    value = os.environ.get("ARIADNE_OFFLINE", "1").lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError("ARIADNE_OFFLINE must be true or false (1 or 0).")


def _host_text(host: object) -> str | None:
    if isinstance(host, bytes):
        try:
            return host.decode("ascii")
        except UnicodeDecodeError:
            return None
    return host if isinstance(host, str) else None


def _is_localhost(host: object) -> bool:
    text = _host_text(host)
    return text is not None and text.lower() in {"localhost", "localhost."}


def _is_loopback(host: object) -> bool:
    text = _host_text(host)
    if text is None:
        return False
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped.is_loopback
    return address.is_loopback


def _denied(operation: str) -> None:
    # Do not echo a remote hostname: URLs may contain private information.
    raise OfflineNetworkError(
        f"Ariadne offline policy blocked {operation} outside numeric loopback. "
        "An owner can set ARIADNE_OFFLINE=0 and restart to enable online connectors."
    )


def _address(sock: object, address: object, operation: str, *, localhost: bool = False):
    family = getattr(sock, "family", None)
    if family == getattr(socket, "AF_UNIX", object()):
        return address
    if family not in {socket.AF_INET, socket.AF_INET6}:
        _denied(operation)
    if not isinstance(address, tuple) or not address:
        _denied(operation)
    host = address[0]
    if localhost and _is_localhost(host):
        # CPython resolves addresses before its connect/sendto audit event.
        # Pass a numeric literal, so localhost cannot resolve to a remote IP.
        return (("::1" if family == socket.AF_INET6 else "127.0.0.1"), *address[1:])
    if not _is_loopback(host):
        _denied(operation)
    return address


def _audit(event: str, args: tuple) -> None:
    if event in {"socket.connect", "socket.sendto", "socket.sendmsg"}:
        # sendmsg(address=None) uses an existing connection, like send/sendall.
        if event == "socket.sendmsg" and args[1] is None:
            return
        _address(args[0], args[1], event)
    elif event in {"socket.getaddrinfo", "socket.gethostbyname"}:
        host = args[0]
        # getaddrinfo(None, ...) is local interface enumeration (AI_PASSIVE),
        # not a remote name lookup. Connect/sendto still reject wildcard IPs.
        # Numeric wildcards are also needed by servers resolving a bind
        # address. They cannot generate DNS, and are never outbound targets.
        if event == "socket.getaddrinfo" and (host is None or _host_text(host) in {"0.0.0.0", "::"}):
            return
        if not (_is_loopback(host) or _is_localhost(host)):
            _denied(event)
    elif event in {"socket.gethostbyaddr", "socket.getnameinfo"}:
        # Reverse lookup may contact DNS even for a loopback address. The
        # getnameinfo audit event omits flags, so NI_NUMERICHOST cannot be
        # distinguished here; fail closed rather than accidentally resolve.
        if event == "socket.getnameinfo" and getattr(_lookup_context, "numeric_nameinfo", False):
            return
        _denied(event)


def _install_socket_wrappers() -> None:
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    sendto = socket.socket.sendto
    getnameinfo = socket.getnameinfo

    def guarded_connect(self, address):
        return connect(self, _address(self, address, "socket.connect", localhost=True))

    def guarded_connect_ex(self, address):
        return connect_ex(self, _address(self, address, "socket.connect_ex", localhost=True))

    def guarded_sendto(self, data, *args):
        if len(args) not in {1, 2}:
            return sendto(self, data, *args)
        destination = _address(self, args[-1], "socket.sendto", localhost=True)
        return sendto(self, data, *args[:-1], destination)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.socket.sendto = guarded_sendto

    def guarded_getnameinfo(address, flags):
        # Waitress uses this for numeric bind metadata. The audit event omits
        # flags, so authorize only this numeric-only call in the current thread.
        numeric_flags = socket.NI_NUMERICHOST | socket.NI_NUMERICSERV
        if (not isinstance(address, tuple) or not address or
                flags & numeric_flags != numeric_flags):
            _denied("socket.getnameinfo")
        host = _host_text(address[0])
        try:
            ipaddress.ip_address(host)
        except (ValueError, TypeError):
            _denied("socket.getnameinfo")
        previous = getattr(_lookup_context, "numeric_nameinfo", False)
        _lookup_context.numeric_nameinfo = True
        try:
            return getnameinfo(address, flags)
        finally:
            _lookup_context.numeric_nameinfo = previous

    socket.getnameinfo = guarded_getnameinfo
    if hasattr(socket.socket, "sendmsg"):
        sendmsg = socket.socket.sendmsg

        def guarded_sendmsg(self, buffers, ancdata=(), flags=0, address=None):
            destination = None if address is None else _address(self, address, "socket.sendmsg", localhost=True)
            return sendmsg(self, buffers, ancdata, flags, destination)

        socket.socket.sendmsg = guarded_sendmsg


def network_policy_status() -> dict:
    """Return the installed policy, without changing it or making requests."""
    return {
        "installed": _installed,
        "enabled": _installed and _enabled,
        "mode": "offline" if _installed and _enabled else ("online" if _installed else "not_installed"),
        "default_offline": True,
        "scope": "python_process",
        "outgoing": "loopback_only" if _installed and _enabled else "unrestricted",
        "incoming": "unchanged",
        "caveats": list(_CAVEATS),
    }


def install_network_policy() -> dict:
    """Install once; default to offline, with an explicit restart-only opt-out.

    CPython audit hooks are irreversible for the process lifetime. Environment
    changes after installation therefore never weaken an active policy.
    """
    global _installed, _enabled
    with _lock:
        if not _installed:
            enabled = _offline_setting()
            if enabled:
                sys.addaudithook(_audit)
                _install_socket_wrappers()
            _enabled = enabled
            _installed = True
    return network_policy_status()
