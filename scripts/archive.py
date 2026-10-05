"""Create a deterministic gzip-9 tar archive and verify its MMDB round trip."""
import argparse
import gzip
import hashlib
from pathlib import Path
import tarfile
import time
from sync import ASSET, ARCHIVE
from validate import source_commit_epoch


def sha256(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def verify_archive(asset, archive, expected_epoch=None):
    asset, archive = Path(asset), Path(archive)
    expected_size = asset.stat().st_size
    expected_digest = sha256(asset)
    # Stream without extracting files onto disk, and read through the gzip CRC trailer.
    with gzip.open(archive, "rb") as compressed:
        with tarfile.open(fileobj=compressed, mode="r|") as bundle:
            member = bundle.next()
            if member is None or not member.isfile() or member.name != ASSET or member.size != expected_size:
                raise ValueError("Archive must contain exactly the expected MMDB file")
            if expected_epoch is not None and member.mtime != expected_epoch:
                raise ValueError("Archive member date must match the source commit timestamp")
            with bundle.extractfile(member) as restored:
                actual_digest = hashlib.file_digest(restored, "sha256").hexdigest()
            if bundle.next() is not None:
                raise ValueError("Archive contains unexpected additional files")
        while compressed.read(1024 * 1024):
            pass
    if actual_digest != expected_digest:
        raise ValueError("Archive MMDB differs from the validated original")
    return {"mmdb_bytes": expected_size, "archive_bytes": archive.stat().st_size,
            "mmdb_sha256": expected_digest, "archive_sha256": sha256(archive)}


def create_archive(asset, build_epoch):
    asset = Path(asset)
    if asset.name != ASSET or not asset.is_file() or asset.stat().st_size == 0:
        raise ValueError("Expected a validated, nonempty firehol-blocklist-ipsets.mmdb")
    if not isinstance(build_epoch, int) or build_epoch < 0:
        raise ValueError("Expected the source commit's nonnegative Unix timestamp")
    archive = asset.with_name(ARCHIVE)
    staged = archive.with_name(archive.name + ".tmp")
    started = time.perf_counter()
    try:
        with staged.open("wb") as output:
            with gzip.GzipFile(filename="", mode="wb", fileobj=output, compresslevel=9, mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.USTAR_FORMAT) as bundle:
                    member = tarfile.TarInfo(ASSET)
                    member.size = asset.stat().st_size
                    member.mtime = build_epoch
                    member.mode = 0o644
                    member.uid = member.gid = 0
                    member.uname = member.gname = ""
                    with asset.open("rb") as source:
                        bundle.addfile(member, source)
        stats = verify_archive(asset, staged, build_epoch)
        staged.replace(archive)
    finally:
        staged.unlink(missing_ok=True)
    reduction = 100 * (1 - stats["archive_bytes"] / stats["mmdb_bytes"])
    print(f"INFO archive_validated file={archive} bytes={stats['archive_bytes']} compression_level=9 reduction_percent={reduction:.2f} elapsed_seconds={time.perf_counter()-started:.3f} round_trip_sha256={stats['mmdb_sha256']}")
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("asset")
    parser.add_argument("source_directory")
    args = parser.parse_args()
    create_archive(args.asset, source_commit_epoch(args.source_directory))
