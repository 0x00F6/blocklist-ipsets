"""Independent MMDB validation using MaxMind's Python reader."""
from collections import Counter
import datetime
import ipaddress
import json
from pathlib import Path
import sys
import subprocess

FIELDS = ("files", "categories", "maintainers", "maintainer_urls", "source_urls", "source_file_dates", "versions")
ALIASES = {"attack": "attacks", "spammer": "spam", "proxies": "proxy", "bot": "botnet", "bots": "botnet", "unallocated": "unroutable", "anonymous": "anonymizers", "anonymizer": "anonymizers"}
CATEGORIES = {"abuse", "attacks", "malware", "spam", "proxy", "botnet", "unroutable", "anonymizers", "other"}
HEADERS = {"category": 1, "maintainer": 2, "maintainer url": 3, "list source url": 4, "source file date": 5, "version": 6}


def source_files(root):
    import os
    root = Path(root)
    for directory, children, files in os.walk(root, followlinks=False):
        children[:] = sorted(child for child in children if not child.startswith(".") and not child.endswith("_country") and not (Path(directory) / child).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix in (".ipset", ".netset") and not path.is_symlink():
                yield path


def normalized(index, value):
    if index == 1:
        value = ALIASES.get(value.lower(), value.lower())
        return value if value in CATEGORIES else "other"
    if index == 5:
        for format_ in ("%a %b %d %H:%M:%S UTC %Y", "%a %b %d %H:%M:%S %Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.datetime.strptime(value, format_).isoformat() + "Z"
            except ValueError:
                pass
    return value


def samples(root):
    """Sample each metadata snapshot and both ends of each sampled CIDR."""
    root = Path(root)
    for path in source_files(root):
        values = [path.relative_to(root).as_posix(), "other", "", "", "", "", ""]
        budget = 3
        with path.open(encoding="utf-8-sig") as source:
            for line in source:
                line = line.strip()
                if line.startswith("#"):
                    key, separator, value = line[1:].partition(":")
                    index = HEADERS.get(key.strip().lower())
                    if separator and index is not None:
                        new = normalized(index, value.strip())
                        if values[index] != new:
                            values[index] = new
                            budget = 3
                    continue
                token = line.split("#", 1)[0].strip()
                if token and budget:
                    network = ipaddress.ip_network(token, strict=False)
                    yield str(network.network_address), tuple(values)
                    yield str(network.broadcast_address), tuple(values)
                    budget -= 1


def source_commit_epoch(root):
    checkout = subprocess.check_output(["git", "-C", str(root), "rev-parse", "--show-toplevel"], text=True).strip()
    if Path(checkout).resolve() != Path(root).resolve():
        raise ValueError("Source directory must be its own Git repository root")
    value = subprocess.check_output(["git", "-C", str(root), "show", "--no-patch", "--format=%ct", "HEAD"], text=True).strip()
    if not value.isascii() or not value.isdecimal():
        raise ValueError("Invalid source commit timestamp")
    return int(value)


def validate(root, database, open_database=None):
    if open_database is None:
        import maxminddb
        open_database = maxminddb.open_database
    count = 0
    with open_database(str(database)) as reader:
        metadata = reader.metadata()
        if metadata.database_type != "firehol-blocklist-ipsets":
            raise ValueError("Unexpected MMDB database type")
        expected_epoch = source_commit_epoch(root)
        if metadata.build_epoch != expected_epoch:
            raise ValueError(f"MMDB build_epoch {metadata.build_epoch} does not match source commit timestamp {expected_epoch}")
        fields = ("database_type", "description", "ip_version", "languages", "build_epoch", "node_count", "record_size", "binary_format_major_version", "binary_format_minor_version")
        print("INFO database_metadata " + json.dumps({field: getattr(metadata, field) for field in fields}, sort_keys=True))
        for ip, expected in samples(root):
            # Conventional MMDB IPv4 compatibility / transition aliases cannot represent
            # independent IPv6 identities in these reserved ranges.
            address = ipaddress.ip_address(ip)
            if address.version == 6 and any(address in ipaddress.ip_network(n) for n in ("::/96", "::ffff:0:0/96", "2002::/16", "2001::/32")):
                continue
            record = reader.get(ip)
            if not isinstance(record, dict) or set(record) != set(FIELDS):
                raise ValueError(f"Invalid MMDB record for {ip}")
            lengths = {len(record[field]) for field in FIELDS if isinstance(record[field], list)}
            if len(lengths) != 1 or not all(isinstance(record[field], list) and all(isinstance(v, str) for v in record[field]) for field in FIELDS):
                raise ValueError(f"Misaligned arrays for {ip}")
            contributions = Counter(zip(*(record[field] for field in FIELDS)))
            if contributions[expected] < 1:
                raise ValueError(f"Missing source contribution for {ip} from {expected[0]}")
            count += 1
    if count == 0:
        raise ValueError("No source addresses were independently validated")
    print(f"INFO independent_validation_passed sample_lookups={count}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: validate.py <blocklist-directory> <database.mmdb>")
    validate(sys.argv[1], sys.argv[2])
