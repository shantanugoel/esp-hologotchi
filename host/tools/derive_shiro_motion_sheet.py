from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .convert_sprite_sheet import RgbaImage, read_rgba_png, write_rgba_png

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_SHEET = REPO_ROOT / "assets" / "sprites" / "shiro_sheet_4x4_128.png"
OUTPUT_SHEET = REPO_ROOT / "assets" / "sprites" / "shiro_motion_4x4_128.png"
CELL = 128

SOURCE_POSES: tuple[str, ...] = (
    "sitting_idle",
    "blink",
    "happy_bounce",
    "tail_wag_left",
    "tail_wag_right",
    "sleeping_curled",
    "alert_ears_up",
    "confused_head_tilt",
    "sad_droop",
    "excited_jump",
    "listening",
    "looking_left",
    "looking_right",
    "paw_lift",
    "surprised",
    "relaxed_loaf",
)


def derive_motion_sheet(
    source_path: Path = SOURCE_SHEET,
    output_path: Path = OUTPUT_SHEET,
    *,
    check: bool = False,
) -> None:
    source = read_rgba_png(source_path)
    cells = {
        pose: _crop_cell(source, index)
        for index, pose in enumerate(SOURCE_POSES)
    }

    derived: tuple[bytes, ...] = (
        _shift(cells["sitting_idle"], 0, -1),
        _shift(cells["sitting_idle"], 0, 1),
        cells["sitting_idle"],
        _shift(cells["sitting_idle"], 0, -1),
        cells["listening"],
        _shift(cells["happy_bounce"], 0, -1),
        _shift(cells["happy_bounce"], 0, 1),
        _shift(cells["paw_lift"], 1, -1),
        _shift(cells["relaxed_loaf"], 0, 1),
        _shift(cells["sleeping_curled"], 0, 1),
        _shift(cells["sad_droop"], -1, 1),
        _mirror_h(cells["confused_head_tilt"]),
        _walk_phase(cells["looking_left"], facing_right=False, phase=1),
        _walk_phase(cells["looking_left"], facing_right=False, phase=-1),
        _walk_phase(cells["looking_right"], facing_right=True, phase=1),
        _walk_phase(cells["looking_right"], facing_right=True, phase=-1),
    )
    image = _compose_sheet(derived, columns=4)
    if check:
        existing = read_rgba_png(output_path)
        if existing != image:
            raise RuntimeError(f"{output_path} is out of date; regenerate motion sheet")
    else:
        write_rgba_png(output_path, image)


def _crop_cell(image: RgbaImage, index: int) -> bytes:
    cell_x = (index % 4) * CELL
    cell_y = (index // 4) * CELL
    out = bytearray(CELL * CELL * 4)
    for y in range(CELL):
        for x in range(CELL):
            _set(out, x, y, image.pixel(cell_x + x, cell_y + y))
    return bytes(out)


def _compose_sheet(cells: Sequence[bytes], *, columns: int) -> RgbaImage:
    if len(cells) % columns != 0:
        raise ValueError("cell count must be a multiple of columns")
    rows = len(cells) // columns
    width = columns * CELL
    height = rows * CELL
    pixels = bytearray(width * height * 4)
    for index, cell in enumerate(cells):
        cell_x = (index % columns) * CELL
        cell_y = (index // columns) * CELL
        for y in range(CELL):
            for x in range(CELL):
                _set_sheet(pixels, width, cell_x + x, cell_y + y, _get(cell, x, y))
    return RgbaImage(width=width, height=height, pixels=bytes(pixels))


def _shift(cell: bytes, dx: int, dy: int) -> bytes:
    out = bytearray(len(cell))
    for y in range(CELL):
        for x in range(CELL):
            pixel = _get(cell, x, y)
            if pixel[3] == 0:
                continue
            _alpha_paste(out, x + dx, y + dy, pixel)
    return bytes(out)


def _mirror_h(cell: bytes) -> bytes:
    out = bytearray(len(cell))
    for y in range(CELL):
        for x in range(CELL):
            _set(out, CELL - 1 - x, y, _get(cell, x, y))
    return bytes(out)


def _walk_phase(cell: bytes, *, facing_right: bool, phase: int) -> bytes:
    """Move only lower-body pixels to create a true leg phase.

    The base side poses are hand-drawn; this keeps the head, ears, collar, body,
    and tail intact while offsetting the leg/paw pixels in opposite directions.
    At 128x128 the alternating lower silhouette reads as walking without adding
    runtime transforms on the ESP32-C3.
    """

    out = bytearray(cell)
    if facing_right:
        x_min, x_max, split = 50, 92, 71
    else:
        x_min, x_max, split = 32, 74, 53
    y_min = 80

    for y in range(y_min, CELL):
        for x in range(x_min, x_max + 1):
            _set(out, x, y, (0, 0, 0, 0))

    for y in range(y_min, CELL):
        for x in range(x_min, x_max + 1):
            pixel = _get(cell, x, y)
            if pixel[3] == 0:
                continue
            side = -1 if x < split else 1
            dx = side * phase * 3
            dy = -1 if (side * phase) > 0 and y > 94 else 0
            _alpha_paste(out, x + dx, y + dy, pixel)

    return _shift(bytes(out), 0, -1 if phase < 0 else 0)


def _get(cell: bytes, x: int, y: int) -> tuple[int, int, int, int]:
    offset = (y * CELL + x) * 4
    r, g, b, a = cell[offset : offset + 4]
    return r, g, b, a


def _set(cell: bytearray, x: int, y: int, pixel: tuple[int, int, int, int]) -> None:
    if 0 <= x < CELL and 0 <= y < CELL:
        offset = (y * CELL + x) * 4
        cell[offset : offset + 4] = bytes(pixel)


def _set_sheet(
    pixels: bytearray, width: int, x: int, y: int, pixel: tuple[int, int, int, int]
) -> None:
    offset = (y * width + x) * 4
    pixels[offset : offset + 4] = bytes(pixel)


def _alpha_paste(
    cell: bytearray, x: int, y: int, pixel: tuple[int, int, int, int]
) -> None:
    if not (0 <= x < CELL and 0 <= y < CELL):
        return
    src_r, src_g, src_b, src_a = pixel
    if src_a == 255:
        _set(cell, x, y, pixel)
        return
    offset = (y * CELL + x) * 4
    dst_r, dst_g, dst_b, dst_a = cell[offset : offset + 4]
    inv = 255 - src_a
    out_a = src_a + (dst_a * inv + 127) // 255
    if out_a == 0:
        cell[offset : offset + 4] = b"\x00\x00\x00\x00"
        return
    out_r = (src_r * src_a + dst_r * dst_a * inv // 255) // out_a
    out_g = (src_g * src_a + dst_g * dst_a * inv // 255) // out_a
    out_b = (src_b * src_a + dst_b * dst_a * inv // 255) // out_a
    cell[offset : offset + 4] = bytes((out_r, out_g, out_b, out_a))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Derive Shiro's supplemental motion sheet from the approved base sheet."
    )
    parser.add_argument("--source", type=Path, default=SOURCE_SHEET)
    parser.add_argument("--output", type=Path, default=OUTPUT_SHEET)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if the derived sheet would differ from --output.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    derive_motion_sheet(args.source, args.output, check=args.check)
    action = "checked" if args.check else "wrote"
    print(f"{action} {args.output.resolve().relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
