"""Future explicit USB identify/configure; never run by agents or a scheduler."""
from pathlib import Path
import argparse
import getpass
import json
import sys
import warnings

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne import ruview_onboarding as onboarding


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default includes unknown argument values in stderr. A user
        # mistakenly supplying a password must not have it echoed into a log.
        super().error("Invalid arguments. Credentials are accepted only through hidden local prompts; use --help.")


def _hidden_credentials():
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ValueError("Use an interactive local console with hidden input; credentials are never accepted in arguments or files.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        ssid = getpass.getpass("Wi-Fi SSID (hidden, not retained): ")
        password = getpass.getpass("Wi-Fi password (hidden, not retained): ")
    return ssid, password


def main(argv=None):
    parser = SafeArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    saved = commands.add_parser("status", help="Read the private journal; no driver import or USB open")
    saved.add_argument("--name", default="xiao-three-node")
    for action in ("identify", "configure"):
        command = commands.add_parser(action)
        command.add_argument("--name", default="xiao-three-node")
        command.add_argument("--node", type=int, required=True, choices=(1, 2, 3))
        command.add_argument("--port", required=True)
        command.add_argument("--request-id", required=True, help="Exclusive usb- identifier; never replay a failed attempt")
        if action == "identify":
            command.add_argument("--open-usb", action="store_true", help="Explicitly open only this COM port and request nonce-bound declarations")
        else:
            command.add_argument("--identification", required=True, help="Completed identification request ID for this node and port")
            command.add_argument("--expected-device", required=True, help="Owner-reviewed 16-hex declared device digest")
            command.add_argument("--target-ip", required=True, help="Explicit RFC1918 IPv4 receiver; does not configure any local listener")
            command.add_argument("--target-port", type=int, required=True)
            command.add_argument("--apply-to-device", action="store_true", help="Explicitly write this selected board's NVS configuration and request firmware reboot")
            command.add_argument("--review-existing-configuration", action="store_true", help="Owner reviewed an existing configured HELLO; this does not clear any failed attempt")
    args = parser.parse_args(argv)
    try:
        if args.action == "status":
            result = onboarding.status(args.name, root=ROOT)
        elif args.action == "identify":
            result = onboarding.identify(args.name, args.node, args.port, args.request_id, root=ROOT, open_usb=args.open_usb)
        else:
            if not args.apply_to_device:
                raise ValueError("Configuration requires --apply-to-device; no USB or credential prompt was opened.")
            intent, _, _ = onboarding.configuration_preflight(args.name, args.node, args.port, args.request_id, args.identification,
                args.expected_device, args.target_ip, args.target_port, root=ROOT,
                review_existing=args.review_existing_configuration)
            ssid, password = _hidden_credentials()
            result = onboarding.configure(args.name, args.node, args.port, args.request_id, args.identification,
                args.expected_device, args.target_ip, args.target_port, ssid, password, root=ROOT,
                apply_to_device=args.apply_to_device, review_existing=args.review_existing_configuration,
                expected_control_revision=intent["control_revision"])
            del ssid, password
    except (ValueError, OSError, ImportError, getpass.GetPassWarning) as error:
        print(json.dumps({"status": "held", "error_type": type(error).__name__,
                          "reason": "Local USB setup did not complete. Inspect the retained attempt/failure phase; no automatic retry is available.",
                          "automatic_retry": False, "local_credential_retention": "none"}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
