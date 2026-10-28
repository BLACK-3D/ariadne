"""Every audit-policy probe runs in a disposable Python subprocess."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NetworkPolicyTests(unittest.TestCase):
    def probe(self, source: str, offline: str | None = None) -> dict:
        environment = os.environ.copy()
        environment.pop("ARIADNE_OFFLINE", None)
        if offline is not None:
            environment["ARIADNE_OFFLINE"] = offline
        prefix = "import sys; sys.path.insert(0, sys.argv[1])\n"
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", prefix + textwrap.dedent(source), str(ROOT)],
            cwd=ROOT, env=environment, capture_output=True, text=True,
            encoding="utf-8", timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout.splitlines()[-1])

    def test_default_policy_is_frozen_idempotent_and_json_safe(self):
        result = self.probe('''
            import json, os, socket, sys
            from ariadne.network_policy import install_network_policy, network_policy_status, OfflineNetworkError
            assert not network_policy_status()['installed']
            initial = install_network_policy()
            first_connect = socket.socket.connect
            os.environ['ARIADNE_OFFLINE'] = '0'
            assert install_network_policy() == initial
            assert socket.socket.connect is first_connect
            with socket.socket() as client:
                try:
                    client.connect(('198.51.100.1', 9))
                except OfflineNetworkError:
                    pass
                else:
                    raise AssertionError('remote connection was not blocked')
            print(json.dumps(network_policy_status()))
        ''')
        self.assertTrue(result["enabled"])
        self.assertEqual(result["scope"], "python_process")
        self.assertEqual(result["incoming"], "unchanged")
        self.assertIn("not an OS firewall", " ".join(result["caveats"]))

    def test_opt_out_is_explicit_and_also_frozen(self):
        for value in ("0", "false", "no", "OFF"):
            with self.subTest(value=value):
                result = self.probe('''
                    import json, os, socket, sys
                    from ariadne.network_policy import install_network_policy
                    original = socket.socket.connect
                    status = install_network_policy()
                    assert socket.socket.connect is original
                    # Synthetic event proves policy absence without a network request.
                    with socket.socket() as client:
                        sys.audit('socket.connect', client, ('198.51.100.1', 9))
                    os.environ['ARIADNE_OFFLINE'] = '1'
                    assert install_network_policy() == status
                    print(json.dumps(status))
                ''', value)
                self.assertEqual(result["mode"], "online")
                self.assertFalse(result["enabled"])

    def test_invalid_boolean_fails_before_installation(self):
        for value in ("", "2", "maybe", " 0", "true "):
            with self.subTest(value=value):
                self.probe('''
                    import json, socket
                    from ariadne.network_policy import install_network_policy, network_policy_status
                    original = socket.socket.connect
                    try:
                        install_network_policy()
                    except ValueError:
                        pass
                    else:
                        raise AssertionError('invalid setting accepted')
                    assert socket.socket.connect is original
                    assert not network_policy_status()['installed']
                    print(json.dumps({'rejected': True}))
                ''', value)

    def test_dns_and_remote_numeric_operations_stop_before_os(self):
        result = self.probe('''
            import json, socket, sys
            from ariadne.network_policy import install_network_policy, OfflineNetworkError
            install_network_policy()
            seen = []
            sys.addaudithook(lambda event, args: seen.append(event) if event.startswith('socket.') else None)
            def blocked(operation):
                seen.clear()
                try:
                    operation()
                except OfflineNetworkError:
                    # A hook registered after the policy never sees its denied
                    # event, so the native operation cannot have continued.
                    assert not any(e in seen for e in ('socket.getaddrinfo', 'socket.connect', 'socket.sendto', 'socket.gethostbyname', 'socket.gethostbyaddr', 'socket.getnameinfo')), seen
                else:
                    raise AssertionError('operation was not blocked')
            for host in ('example.invalid', b'example.invalid', '198.51.100.1', '192.168.1.1', '127.1', '::ffff:198.51.100.1'):
                blocked(lambda: socket.getaddrinfo(host, 9))
            blocked(lambda: socket.gethostbyname('example.invalid'))
            blocked(lambda: socket.gethostbyname_ex('example.invalid'))
            blocked(lambda: socket.gethostbyaddr('127.0.0.1'))
            blocked(lambda: socket.getnameinfo(('127.0.0.1', 9), 0))
            with socket.socket() as client:
                for host in ('example.invalid', '198.51.100.1', '0.0.0.0', '192.168.1.1'):
                    blocked(lambda: client.connect((host, 9)))
                    blocked(lambda: client.connect_ex((host, 9)))
            with socket.socket(type=socket.SOCK_DGRAM) as client:
                blocked(lambda: client.sendto(b'x', ('example.invalid', 9)))
                blocked(lambda: client.sendto(b'x', 0, ('198.51.100.1', 9)))
            print(json.dumps({'blocked': True}))
        ''')
        self.assertTrue(result["blocked"])

    def test_wrappers_never_resolve_hostname_and_normalize_localhost(self):
        self.probe('''
            import json, socket
            from ariadne.network_policy import install_network_policy, OfflineNetworkError
            calls = []
            # This stand-in for the C method is the only execution boundary.
            # No resolver or socket operation is needed for this test.
            def original(self, address):
                calls.append(address)
            socket.socket.connect = original
            socket.socket.connect_ex = original
            install_network_policy()
            with socket.socket() as client:
                client.connect(('localhost', 9))
                client.connect_ex((b'LOCALHOST.', 10))
                try:
                    client.connect(('example.invalid', 9))
                except OfflineNetworkError:
                    pass
                else:
                    raise AssertionError('hostname reached native boundary')
            assert calls == [('127.0.0.1', 9), ('127.0.0.1', 10)], calls
            print(json.dumps({'normalized': True}))
        ''')

    def test_audit_checks_low_level_destinations_ipv6_and_unix(self):
        self.probe('''
            import json, socket, sys, types
            from ariadne.network_policy import install_network_policy, OfflineNetworkError
            install_network_policy()
            inet6 = types.SimpleNamespace(family=socket.AF_INET6)
            for host in ('::1', '::ffff:127.0.0.1', '::ffff:127.255.255.255'):
                sys.audit('socket.connect', inet6, (host, 9, 0, 0))
            for host in ('::', '2001:db8::1', '::ffff:192.168.1.1', 'localhost'):
                try:
                    sys.audit('socket.connect', inet6, (host, 9, 0, 0))
                except OfflineNetworkError:
                    pass
                else:
                    raise AssertionError('nonliteral or remote host accepted')
            if hasattr(socket, 'AF_UNIX'):
                unix = types.SimpleNamespace(family=socket.AF_UNIX)
                sys.audit('socket.connect', unix, 'local.sock')
                sys.audit('socket.sendto', unix, b'local.sock')
            sys.audit('socket.bind', inet6, ('::', 0, 0, 0))
            print(json.dumps({'checked': True}))
        ''')

    def test_loopback_tcp_udp_and_localhost_still_work(self):
        self.probe('''
            import json, socket
            from ariadne.network_policy import install_network_policy
            install_network_policy()
            # Resolution is allowed for localhost; every resolved destination
            # still passes the numeric connect check independently.
            assert socket.getaddrinfo('localhost', 9)
            assert socket.getaddrinfo(None, 0, socket.AF_INET, socket.SOCK_STREAM, 0, socket.AI_PASSIVE)
            with socket.socket() as listener:
                listener.settimeout(3)
                listener.bind(('127.0.0.1', 0))
                listener.listen(4)
                port = listener.getsockname()[1]
                for host in ('127.0.0.1', 'localhost'):
                    with socket.socket() as client:
                        client.settimeout(3)
                        client.connect((host, port))
                        with listener.accept()[0] as accepted:
                            accepted.sendall(b'ok')
                            assert client.recv(2) == b'ok'
                with socket.create_connection(('localhost', port), timeout=3) as client:
                    with listener.accept()[0] as accepted:
                        accepted.sendall(b'ok')
                        assert client.recv(2) == b'ok'
            with socket.socket(type=socket.SOCK_DGRAM) as receiver, socket.socket(type=socket.SOCK_DGRAM) as sender:
                receiver.settimeout(3)
                receiver.bind(('127.0.0.1', 0))
                port = receiver.getsockname()[1]
                sender.sendto(b'one', ('127.0.0.1', port))
                assert receiver.recvfrom(10)[0] == b'one'
                sender.sendto(b'two', 0, ('localhost', port))
                assert receiver.recvfrom(10)[0] == b'two'
            print(json.dumps({'tcp': True, 'udp': True}))
        ''')

    def test_waitress_can_bind_all_interfaces(self):
        self.probe('''
            import json, socket
            from ariadne.network_policy import install_network_policy
            from waitress.server import create_server
            install_network_policy()
            assert socket.getaddrinfo('0.0.0.0', 0, socket.AF_INET, socket.SOCK_STREAM)
            assert socket.getaddrinfo('::', 0, socket.AF_INET6, socket.SOCK_STREAM)
            numeric_flags = socket.NI_NUMERICHOST | socket.NI_NUMERICSERV
            assert socket.getnameinfo(('0.0.0.0', 9), numeric_flags) == ('0.0.0.0', '9')
            assert socket.getnameinfo(('198.51.100.1', 9), numeric_flags) == ('198.51.100.1', '9')
            def application(environ, start_response):
                start_response('200 OK', [('Content-Type', 'text/plain')])
                return [b'ok']
            channels = {}
            server = create_server(application, host='0.0.0.0', port=0, threads=1, map=channels)
            try:
                assert server.socket.getsockname()[0] == '0.0.0.0'
                assert server.socket.getsockname()[1] > 0
            finally:
                server.task_dispatcher.shutdown()
                for channel in list(channels.values()):
                    channel.close()
            print(json.dumps({'lan_bind': True}))
        ''')


if __name__ == "__main__":
    unittest.main()
