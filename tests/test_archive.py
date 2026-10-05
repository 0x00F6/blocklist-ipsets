import gzip
import io
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from archive import create_archive, verify_archive
from sync import ASSET, ARCHIVE

EPOCH = 1_791_191_852


class ArchiveTests(unittest.TestCase):
    def test_gzip9_round_trip_and_reproducibility_ignore_filesystem_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / ASSET
            asset.write_bytes(b"MMDB fixture " * 1024)
            archive = create_archive(asset, EPOCH)
            first = archive.read_bytes()
            self.assertEqual(first[8], 2)  # gzip XFL: maximum compression.
            self.assertEqual(first[4:8], b"\0\0\0\0")
            os.utime(asset, (1, 1))
            create_archive(asset, EPOCH)
            self.assertEqual(first, archive.read_bytes())
            stats = verify_archive(asset, archive, EPOCH)
            self.assertEqual(stats["mmdb_bytes"], asset.stat().st_size)
            with tarfile.open(archive, "r:gz") as bundle:
                member, = bundle.getmembers()
                self.assertEqual((member.name, member.mtime, member.uid, member.gid, member.mode), (ASSET, EPOCH, 0, 0, 0o644))

    def test_corrupt_gzip_trailer_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / ASSET
            asset.write_bytes(b"mmdb")
            archive = create_archive(asset, EPOCH)
            damaged = bytearray(archive.read_bytes())
            damaged[-8] ^= 1
            archive.write_bytes(damaged)
            with self.assertRaises((gzip.BadGzipFile, EOFError)):
                verify_archive(asset, archive, EPOCH)

    def test_extra_members_and_unsafe_names_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / ASSET
            asset.write_bytes(b"mmdb")
            archive = asset.with_name(ARCHIVE)
            for names in ((ASSET, "extra.txt"), ("../" + ASSET,)):
                with tarfile.open(archive, "w:gz") as bundle:
                    for name in names:
                        member = tarfile.TarInfo(name)
                        member.size = 4
                        member.mtime = EPOCH
                        bundle.addfile(member, io.BytesIO(b"mmdb"))
                with self.assertRaises(ValueError):
                    verify_archive(asset, archive, EPOCH)

    def test_failed_validation_preserves_previous_archive(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / ASSET
            asset.write_bytes(b"mmdb")
            archive = asset.with_name(ARCHIVE)
            archive.write_bytes(b"previous archive")
            with patch('archive.verify_archive', side_effect=ValueError('validation failed')):
                with self.assertRaises(ValueError):
                    create_archive(asset, EPOCH)
            self.assertEqual(archive.read_bytes(), b"previous archive")
            self.assertFalse(archive.with_name(archive.name + '.tmp').exists())


if __name__ == "__main__":
    unittest.main()
