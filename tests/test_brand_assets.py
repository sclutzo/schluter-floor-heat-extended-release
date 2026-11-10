"""Ensure shipped custom integration brand files are valid PNGs.

No third-party imaging library needed; validate CRC for every PNG chunk and
the full compressed image stream, so a damaged image cannot pass CI.
"""

from pathlib import Path
import struct
import unittest
import zlib


ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "custom_components" / "schluter_heat_extended" / "brand"


def read_png(path):
    payload = path.read_bytes()
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("PNG signature invalid")
    cursor, compressed, image_size = 8, bytearray(), None
    seen_end = False
    while cursor < len(payload):
        if cursor + 12 > len(payload):
            raise ValueError("Truncated PNG chunk")
        length = struct.unpack(">I", payload[cursor:cursor + 4])[0]
        tag = payload[cursor + 4:cursor + 8]
        end = cursor + 12 + length
        if end > len(payload):
            raise ValueError("Truncated PNG payload")
        data = payload[cursor + 8:cursor + 8 + length]
        expected = struct.unpack(">I", payload[end - 4:end])[0]
        if zlib.crc32(tag + data) != expected:
            raise ValueError("Invalid PNG chunk CRC")
        if tag == b"IHDR":
            image_size = struct.unpack(">II", data[:8])
        if tag == b"IDAT":
            compressed.extend(data)
        if tag == b"IEND":
            seen_end = True
            if end != len(payload):
                raise ValueError("Unexpected trailing PNG data")
        cursor = end
    if not seen_end or image_size is None or not compressed:
        raise ValueError("Incomplete PNG")
    zlib.decompress(bytes(compressed))
    return image_size


class BrandFilesTest(unittest.TestCase):
    def test_icon_and_logo_exist_and_decode(self):
        self.assertEqual(read_png(BRAND / "icon.png"), (256, 256))
        self.assertEqual(read_png(BRAND / "logo.png"), (256, 256))


if __name__ == "__main__":
    unittest.main()
