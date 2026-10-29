"""Run with python -m ariadne. No package installation required."""
import argparse
import json
import logging
import os
from pathlib import Path
import socketserver
import sys
import threading
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server

from . import __version__
from .config import load_settings
from .model import Ollama
from .server import Application


class LocalServer(socketserver.ThreadingMixIn, WSGIServer):
    daemon_threads = True
    request_queue_size = 32
    block_on_close = False

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(20)
        return sock, address


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        # Never log full URLs, query strings, payloads or bearer tokens.
        pass


def _run_server(server, stop_event, *, waitress=False):
    """Serve normally, or honor an owned host's local shutdown event."""
    run = server.run if waitress else server.serve_forever
    if stop_event is None:
        run()
        return
    errors = []
    def serve():
        try:
            run()
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=serve, name="ariadne-http", daemon=True)
    thread.start()
    while thread.is_alive() and not stop_event.wait(.1):
        pass
    if stop_event.is_set():
        if waitress:
            server.task_dispatcher.shutdown(timeout=5)
            server.close()
            server.asyncore.close_all(server._map)
        else:
            server.shutdown()
        thread.join(timeout=7)
        if thread.is_alive():
            raise ValueError("HTTP server did not complete graceful shutdown.")
    if errors and not (stop_event.is_set() and isinstance(errors[0], OSError)):
        raise errors[0]


def main(on_ready=None, *, stop_event=None, show_pairing_token=True):
    parser = argparse.ArgumentParser(description="Ariadne local AI server")
    parser.add_argument("command", choices=["serve", "doctor", "token"], nargs="?", default="serve")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        from .box_profiles import bootstrap_profiles
        bootstrap_profiles(Path(__file__).resolve().parents[1], Path(os.environ.get("ARIADNE_DATA_DIR", "data")))
        settings = load_settings()
        from .network_policy import install_network_policy
        install_network_policy()
        if args.command == "token":
            print(settings.api_token)
            return
        if args.command == "doctor":
            model = Ollama(settings.ollama_url, settings.model_timeout)
            status = model.status()
            print(json.dumps({"python": sys.version.split()[0], "workspace": str(settings.workspace), "model": settings.model,
                              "ollama": status, "ready": status["available"] and settings.model in {m["name"] for m in status["models"]}}, indent=2))
            return
        app = Application(settings)
        app.engine.start()
        try:
            app.start_services()
            print(f"Ariadne {__version__} | http://{settings.host}:{settings.port}", flush=True)
            if show_pairing_token:
                print(f"Pairing token: {settings.api_token}", flush=True)
            else:
                print("Pairing key is stored only in data/server.token.", flush=True)
            print(f"Private workspace: {settings.workspace}", flush=True)
            print("Ctrl+C stops the server. Missions execute only within configured capabilities.", flush=True)
            try:
                from waitress import create_server
            except ImportError:
                if settings.host not in {"localhost", "127.0.0.1", "::1"}:
                    raise ValueError("LAN serving requires Waitress. Install the pinned server extra or use Docker.")
                with make_server(settings.host, settings.port, app, server_class=LocalServer, handler_class=QuietHandler) as server:
                    if on_ready:
                        on_ready()
                    _run_server(server, stop_event)
            else:
                server = create_server(app, host=settings.host, port=settings.port, threads=8, connection_limit=64, max_request_body_size=14_000_000, map={})
                try:
                    if on_ready:
                        on_ready()
                    _run_server(server, stop_event, waitress=True)
                finally:
                    server.close()
        finally:
            try:
                app.stop_services()
            finally:
                app.engine.stop()
    except KeyboardInterrupt:
        pass
    except (ValueError, OSError) as error:
        print(f"Startup error: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
