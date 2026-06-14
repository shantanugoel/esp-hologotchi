from __future__ import annotations

import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from host.tools.convert_sprite_sheet import convert_sheet, read_rgba_png


class SpriteSheetConverterTests(unittest.TestCase):
    def test_reads_filter_zero_rgba_png(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tiny.png"
            _write_rgba_png(path, 1, 1, [(255, 0, 0, 255)])

            image = read_rgba_png(path)

        self.assertEqual(image.width, 1)
        self.assertEqual(image.height, 1)
        self.assertEqual(image.pixel(0, 0), (255, 0, 0, 255))

    def test_conversion_is_deterministic_and_uses_pose_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sheet = root / "tiny.png"
            metadata = root / "tiny.json"
            output = root / "tiny.rs"
            _write_rgba_png(
                sheet,
                4,
                2,
                [
                    (0, 0, 0, 0),
                    (255, 255, 255, 255),
                    (255, 0, 0, 255),
                    (0, 0, 0, 0),
                    (0, 0, 0, 255),
                    (250, 32, 24, 255),
                    (20, 20, 20, 8),
                    (255, 255, 255, 255),
                ],
            )
            metadata.write_text(
                json.dumps(
                    {
                        "name": "tiny",
                        "sheet": "tiny.png",
                        "columns": 2,
                        "rows": 1,
                        "cell_width": 2,
                        "cell_height": 2,
                        "poses": ["sitting_idle", "tail_wag_left"],
                    }
                ),
                encoding="utf-8",
            )

            stats = convert_sheet(sheet, metadata, output)
            first = output.read_text(encoding="utf-8")
            convert_sheet(sheet, metadata, output)
            second = output.read_text(encoding="utf-8")

        self.assertEqual(first, second)
        self.assertEqual(stats.sprite_count, 2)
        self.assertIn("pub enum PoseId", first)
        self.assertIn("SittingIdle = 0", first)
        self.assertIn("TailWagLeft = 1", first)
        self.assertIn("pub const PALETTE_RGB565", first)
        self.assertIn("SOLID_RUN_FLAG", first)


def _write_rgba_png(path: Path, width: int, height: int, pixels: list[tuple[int, int, int, int]]) -> None:
    if len(pixels) != width * height:
        raise ValueError("pixel count does not match dimensions")
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for pixel in pixels[y * width : (y + 1) * width]:
            rows.extend(pixel)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFF_FFFF)
        )

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk("IHDR".encode(), struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk("IDAT".encode(), zlib.compress(bytes(rows)))
        + chunk("IEND".encode(), b"")
    )
