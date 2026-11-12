"""Send allowlisted settings over SSH stdin; never display their values."""

import argparse
import re
import subprocess
from pathlib import Path

ALLOWLIST = (
    "PAYPAL_CLIENT_ID",
    "PAYPAL_CLIENT_SECRET",
    "PAYPAL_ENV",
    "PAYPAL_WEBHOOK_ID",
    "VULTR_INFERENCE_API_KEY",
    "OPENAI_API_KEY",
    "REFUNDOPS_CREDENTIAL_KEY",
    "REFUNDOPS_STRONG_MODEL",
    "REFUNDOPS_FAST_MODEL",
    "REFUNDOPS_EVIDENCE_MODEL",
    "DJANGO_SECRET_KEY",
)
HOST_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?")
REMOTE_COMMAND = """set -eu
umask 027
test -d /etc/refundops
target=$(mktemp /etc/refundops/refundops.env.XXXXXX)
trap 'rm -f "$target"' EXIT HUP INT TERM
cat > "$target"
chown root:refundops "$target"
chmod 0640 "$target"
mv -f "$target" /etc/refundops/refundops.env
"""


def validate_host(host: str) -> str:
    if len(host) > 253 or not HOST_PATTERN.fullmatch(host):
        raise ValueError("Expected a DNS hostname without a scheme or port.")
    return host


def parse_value(name: str, raw: str) -> str:
    """Accept single-line dotenv literals, without variable interpolation."""
    raw = raw.strip()
    if raw.startswith(("'", '"')):
        quote = raw[0]
        characters: list[str] = []
        index = 1
        while index < len(raw):
            char = raw[index]
            if char == quote:
                tail = raw[index + 1 :].strip()
                if tail and not tail.startswith("#"):
                    raise ValueError(f"Invalid quoted value: {name}")
                return "".join(characters)
            if char == "\\" and index + 1 < len(raw) and raw[index + 1] in (quote, "\\"):
                index += 1
                char = raw[index]
            characters.append(char)
            index += 1
        raise ValueError(f"Unclosed quoted value: {name}")
    return re.split(r"\s+#", raw, maxsplit=1)[0].rstrip()


def read_local_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, raw = line.partition("=")
        name = name.strip()
        if separator and name in ALLOWLIST:
            if name in values:
                raise ValueError(f"Duplicate key: {name}")
            values[name] = parse_value(name, raw)
    return values


def build_env_file(local: dict[str, str], host: str) -> str:
    host = validate_host(host)
    missing = [name for name in ALLOWLIST if not local.get(name, "").strip()]
    if missing:
        raise ValueError("Missing required keys: " + ", ".join(missing))
    values = {name: local[name] for name in ALLOWLIST}
    values.update(
        DJANGO_DEBUG="false",
        DJANGO_ALLOWED_HOSTS=f"{host},localhost,.localhost",
        REFUNDOPS_SITE_HOSTS="portal.localhost,tracking.localhost,inbox.localhost,store.localhost",
    )
    lines: list[str] = []
    for name, value in values.items():
        if any(char in value for char in ("\n", "\r", "\x00")):
            raise ValueError(f"Only single-line values are supported: {name}")
        # EnvironmentFile is not a shell: $ and % stay literal here.
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{name}="{escaped}"')
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", help="root@<VM_IP>")
    parser.add_argument("public_host", help="<PUBLIC_HOST>, without scheme or port")
    parser.add_argument(
        "--env-file", type=Path, default=Path(__file__).resolve().parents[2] / ".env"
    )
    args = parser.parse_args()
    try:
        if not args.target.startswith("root@"):
            raise ValueError("SSH target must be root@ followed by a hostname or IPv4 address.")
        validate_host(args.target[5:])
        content = build_env_file(read_local_env(args.env_file), args.public_host)
    except (OSError, UnicodeError):
        raise SystemExit("Cannot read the local environment file.") from None
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", args.target, REMOTE_COMMAND],
            input=content,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SystemExit("SSH transfer failed; check SSH access separately.") from None
    if result.returncode:
        raise SystemExit("SSH transfer failed; check SSH access and bootstrap separately.")
    print("Wrote keys: " + ", ".join((*ALLOWLIST, "DJANGO_DEBUG", "DJANGO_ALLOWED_HOSTS", "REFUNDOPS_SITE_HOSTS")))


if __name__ == "__main__":
    main()
