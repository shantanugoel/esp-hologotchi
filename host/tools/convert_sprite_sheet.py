from __future__ import annotations

import argparse
import json
import re
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SHEET = REPO_ROOT / "assets" / "sprites" / "shiro_sheet_4x4_128.png"
DEFAULT_METADATA = REPO_ROOT / "assets" / "sprites" / "shiro_sheet.json"
DEFAULT_OUTPUT = REPO_ROOT / "device" / "src" / "shiro_sprites.rs"
DEFAULT_ALPHA_THRESHOLD = 12

# A small fixed RGB565 palette tuned for Shiro's current sheet:
# black outline shades, white body levels, and saturated/dark collar reds.
DEFAULT_PALETTE_RGB565: tuple[int, ...] = (
    0x0000,
    0x0841,
    0x1082,
    0x18C3,
    0x2104,
    0x3186,
    0x4208,
    0x5ACB,
    0x738E,
    0x8C71,
    0xA514,
    0xBDF7,
    0xCE59,
    0xDEDB,
    0xE73C,
    0xEF7D,
    0xF7BE,
    0xFFFF,
    0x6000,
    0x8800,
    0xA8A2,
    0xC103,
    0xE924,
    0xF944,
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SOLID_RUN_FLAG = 0x80
MAX_RUN_LEN = 128


@dataclass(frozen=True)
class RgbaImage:
    width: int
    height: int
    pixels: bytes

    def pixel(self, x: int, y: int) -> tuple[int, int, int, int]:
        offset = (y * self.width + x) * 4
        r, g, b, a = self.pixels[offset : offset + 4]
        return r, g, b, a


@dataclass(frozen=True)
class SheetMeta:
    name: str
    cell_width: int
    cell_height: int
    sheets: tuple["SheetSpec", ...]
    explicit_sheets: bool


@dataclass(frozen=True)
class SheetSpec:
    sheet: str
    columns: int
    rows: int
    poses: tuple[str, ...]


@dataclass(frozen=True)
class SpriteRust:
    pose: str
    const_name: str
    enum_name: str
    x: int
    y: int
    width: int
    height: int
    data: tuple[int, ...]
    visible_pixels: int


@dataclass(frozen=True)
class ConversionStats:
    sprite_count: int
    visible_pixels: int
    rle_bytes: int
    palette_size: int


def read_rgba_png(path: Path) -> RgbaImage:
    raw = path.read_bytes()
    if not raw.startswith(PNG_SIGNATURE):
        raise ValueError(f"{path} is not a PNG file")

    pos = len(PNG_SIGNATURE)
    width = height = bit_depth = color_type = interlace = None
    idat: list[bytes] = []
    while pos < len(raw):
        if pos + 12 > len(raw):
            raise ValueError(f"{path} has a truncated PNG chunk")
        length = struct.unpack(">I", raw[pos : pos + 4])[0]
        chunk_type = raw[pos + 4 : pos + 8]
        chunk = raw[pos + 8 : pos + 8 + length]
        crc = raw[pos + 8 + length : pos + 12 + length]
        if len(chunk) != length or len(crc) != 4:
            raise ValueError(f"{path} has a truncated {chunk_type!r} chunk")
        expected_crc = zlib.crc32(chunk_type + chunk) & 0xFFFF_FFFF
        actual_crc = struct.unpack(">I", crc)[0]
        if expected_crc != actual_crc:
            raise ValueError(f"{path} has a bad {chunk_type.decode('ascii')} CRC")
        pos += 12 + length

        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _comp, _filter, interlace = (
                struct.unpack(">IIBBBBB", chunk)
            )
        elif chunk_type == b"IDAT":
            idat.append(chunk)
        elif chunk_type == b"IEND":
            break

    if (width, height, bit_depth, color_type, interlace).count(None) != 0:
        raise ValueError(f"{path} is missing IHDR")
    if bit_depth != 8 or color_type != 6 or interlace != 0:
        raise ValueError(
            f"{path} must be 8-bit non-interlaced RGBA; "
            f"got bit_depth={bit_depth}, color_type={color_type}, interlace={interlace}"
        )

    decompressed = zlib.decompress(b"".join(idat))
    stride = width * 4
    expected = height * (stride + 1)
    if len(decompressed) != expected:
        raise ValueError(f"{path} decompressed to {len(decompressed)} bytes, expected {expected}")

    rows: list[bytes] = []
    prev = bytes(stride)
    offset = 0
    for _y in range(height):
        filter_type = decompressed[offset]
        offset += 1
        scanline = decompressed[offset : offset + stride]
        offset += stride
        row = _unfilter_scanline(filter_type, scanline, prev, bpp=4)
        rows.append(row)
        prev = row

    return RgbaImage(width=width, height=height, pixels=b"".join(rows))


def write_rgba_png(path: Path, image: RgbaImage) -> None:
    """Write an 8-bit non-interlaced RGBA PNG using filter type 0.

    The converter intentionally stays dependency-free so sprite regeneration is
    not coupled to Pillow or ImageMagick availability.
    """

    rows = bytearray()
    stride = image.width * 4
    if len(image.pixels) != image.height * stride:
        raise ValueError("image pixel buffer does not match dimensions")
    for y in range(image.height):
        rows.append(0)
        start = y * stride
        rows.extend(image.pixels[start : start + stride])

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        PNG_SIGNATURE
        + _png_chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", image.width, image.height, 8, 6, 0, 0, 0),
        )
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows), level=9))
        + _png_chunk(b"IEND", b"")
    )


def load_metadata(path: Path) -> SheetMeta:
    raw = json.loads(path.read_text(encoding="utf-8"))
    try:
        name = str(raw["name"])
        cell_width = int(raw["cell_width"])
        cell_height = int(raw["cell_height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{path} is not valid sprite metadata") from exc

    if cell_width <= 0 or cell_height <= 0:
        raise ValueError("cell dimensions must be positive")

    explicit_sheets = "sheets" in raw
    if explicit_sheets:
        sheets = tuple(_parse_sheet_spec(item) for item in raw["sheets"])
    else:
        sheets = (
            _parse_sheet_spec(
                {
                    "sheet": raw["sheet"],
                    "columns": raw["columns"],
                    "rows": raw["rows"],
                    "poses": raw["poses"],
                }
            ),
        )
    if not sheets:
        raise ValueError("metadata must contain at least one sheet")

    poses = tuple(pose for sheet in sheets for pose in sheet.poses)
    if len(set(poses)) != len(poses):
        raise ValueError("pose names must be unique")
    return SheetMeta(
        name=name,
        cell_width=cell_width,
        cell_height=cell_height,
        sheets=sheets,
        explicit_sheets=explicit_sheets,
    )


def convert_sheet(
    sheet_path: Path = DEFAULT_SHEET,
    metadata_path: Path = DEFAULT_METADATA,
    output_path: Path = DEFAULT_OUTPUT,
    *,
    alpha_threshold: int = DEFAULT_ALPHA_THRESHOLD,
    palette_rgb565: Sequence[int] = DEFAULT_PALETTE_RGB565,
    check: bool = False,
) -> ConversionStats:
    if not 1 <= alpha_threshold <= 255:
        raise ValueError("alpha_threshold must be in 1..255")
    if len(palette_rgb565) > 128:
        raise ValueError("RLE solid runs reserve one command bit; palette size must be <= 128")

    meta = load_metadata(metadata_path)
    sprites = tuple(
        _encode_all_sprites(
            sheet_path,
            metadata_path,
            meta,
            palette_rgb565=palette_rgb565,
            alpha_threshold=alpha_threshold,
        )
    )
    rust = render_rust_module(
        meta,
        sprites,
        palette_rgb565=tuple(palette_rgb565),
        sheet_path=sheet_path,
        metadata_path=metadata_path,
        alpha_threshold=alpha_threshold,
    )

    if check:
        existing = output_path.read_text(encoding="utf-8")
        if existing != rust:
            raise RuntimeError(f"{output_path} is out of date; regenerate sprite assets")
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rust, encoding="utf-8")

    return ConversionStats(
        sprite_count=len(sprites),
        visible_pixels=sum(sprite.visible_pixels for sprite in sprites),
        rle_bytes=sum(len(sprite.data) for sprite in sprites),
        palette_size=len(palette_rgb565),
    )


def render_rust_module(
    meta: SheetMeta,
    sprites: Sequence[SpriteRust],
    *,
    palette_rgb565: tuple[int, ...],
    sheet_path: Path,
    metadata_path: Path,
    alpha_threshold: int,
) -> str:
    lines: list[str] = [
        "// @generated by host/tools/convert_sprite_sheet.py. Do not edit by hand.",
        f"// Source sheets: {', '.join(_display_path(path) for path in _source_paths(sheet_path, metadata_path, meta))}",
        f"// Metadata: {_display_path(metadata_path)}",
        f"// Alpha threshold: {alpha_threshold}",
        "//! Fixed Shiro sprite assets, packed as transparent/solid RLE streams.",
        "",
        "#![allow(dead_code)]",
        "#![allow(clippy::unreadable_literal)]",
        "",
        "#[derive(Clone, Copy, Debug, Eq, PartialEq)]",
        "#[repr(usize)]",
        "pub enum PoseId {",
    ]
    for index, sprite in enumerate(sprites):
        lines.append(f"    {sprite.enum_name} = {index},")
    lines.extend(
        [
            "}",
            "",
            "impl PoseId {",
            "    #[inline]",
            "    pub const fn as_index(self) -> usize {",
            "        self as usize",
            "    }",
            "}",
            "",
            "#[derive(Clone, Copy, Debug, Eq, PartialEq)]",
            "pub struct Sprite {",
            "    pub x: i16,",
            "    pub y: i16,",
            "    pub width: u8,",
            "    pub height: u8,",
            "    pub data: &'static [u8],",
            "}",
            "",
            f"pub const CELL_WIDTH: u8 = {meta.cell_width};",
            f"pub const CELL_HEIGHT: u8 = {meta.cell_height};",
            f"pub const POSE_COUNT: usize = {len(sprites)};",
            "pub const SOLID_RUN_FLAG: u8 = 0x80;",
            "pub const RUN_LEN_MASK: u8 = 0x7F;",
            "",
            "#[rustfmt::skip]",
            f"pub const PALETTE_RGB565: [u16; {len(palette_rgb565)}] = [",
            _format_int_list(palette_rgb565, hex_width=4, indent="    "),
            "];",
            "",
        ]
    )

    for sprite in sprites:
        lines.extend(
            [
                "#[rustfmt::skip]",
                f"const {sprite.const_name}: &[u8] = &[",
                _format_int_list(sprite.data, hex_width=2, indent="    "),
                "];",
                "",
            ]
        )

    lines.extend(
        [
            "#[rustfmt::skip]",
            f"pub const SPRITES: [Sprite; {len(sprites)}] = [",
        ]
    )
    for sprite in sprites:
        lines.append(
            "    Sprite { "
            f"x: {sprite.x}, y: {sprite.y}, width: {sprite.width}, "
            f"height: {sprite.height}, data: {sprite.const_name} "
            "},"
        )
    lines.extend(
        [
            "];",
            "",
            "#[inline]",
            "pub fn sprite(pose: PoseId) -> &'static Sprite {",
            "    &SPRITES[pose.as_index()]",
            "}",
            "",
        ]
    )
    return "\n".join(lines)


def _unfilter_scanline(filter_type: int, scanline: bytes, prev: bytes, *, bpp: int) -> bytes:
    out = bytearray(len(scanline))
    for index, encoded in enumerate(scanline):
        left = out[index - bpp] if index >= bpp else 0
        up = prev[index]
        upper_left = prev[index - bpp] if index >= bpp else 0
        if filter_type == 0:
            value = encoded
        elif filter_type == 1:
            value = encoded + left
        elif filter_type == 2:
            value = encoded + up
        elif filter_type == 3:
            value = encoded + ((left + up) // 2)
        elif filter_type == 4:
            value = encoded + _paeth(left, up, upper_left)
        else:
            raise ValueError(f"unsupported PNG filter type {filter_type}")
        out[index] = value & 0xFF
    return bytes(out)


def _paeth(left: int, up: int, upper_left: int) -> int:
    estimate = left + up - upper_left
    left_dist = abs(estimate - left)
    up_dist = abs(estimate - up)
    upper_left_dist = abs(estimate - upper_left)
    if left_dist <= up_dist and left_dist <= upper_left_dist:
        return left
    if up_dist <= upper_left_dist:
        return up
    return upper_left


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFF_FFFF)
    )


def _parse_sheet_spec(raw: object) -> SheetSpec:
    if not isinstance(raw, dict):
        raise ValueError("sheet entry must be an object")
    try:
        spec = SheetSpec(
            sheet=str(raw["sheet"]),
            columns=int(raw["columns"]),
            rows=int(raw["rows"]),
            poses=tuple(str(pose) for pose in raw["poses"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("sheet entry is missing required fields") from exc

    if spec.columns <= 0 or spec.rows <= 0:
        raise ValueError("columns and rows must be positive")
    expected_poses = spec.columns * spec.rows
    if len(spec.poses) != expected_poses:
        raise ValueError(
            f"sheet {spec.sheet!r} has {len(spec.poses)} poses, expected {expected_poses}"
        )
    return spec


def _encode_all_sprites(
    sheet_path: Path,
    metadata_path: Path,
    meta: SheetMeta,
    *,
    palette_rgb565: Sequence[int],
    alpha_threshold: int,
) -> Iterable[SpriteRust]:
    for path, spec in zip(_source_paths(sheet_path, metadata_path, meta), meta.sheets):
        image = read_rgba_png(path)
        _validate_sheet_shape(path, image, meta, spec)
        for pose_index in range(len(spec.poses)):
            yield _encode_sprite(
                image,
                meta,
                spec,
                pose_index=pose_index,
                palette_rgb565=palette_rgb565,
                alpha_threshold=alpha_threshold,
            )


def _source_paths(sheet_path: Path, metadata_path: Path, meta: SheetMeta) -> tuple[Path, ...]:
    if not meta.explicit_sheets:
        return (sheet_path,)
    return tuple((metadata_path.parent / sheet.sheet).resolve() for sheet in meta.sheets)


def _validate_sheet_shape(
    sheet_path: Path, image: RgbaImage, meta: SheetMeta, spec: SheetSpec
) -> None:
    expected_width = spec.columns * meta.cell_width
    expected_height = spec.rows * meta.cell_height
    if image.width != expected_width or image.height != expected_height:
        raise ValueError(
            f"{sheet_path} is {image.width}x{image.height}, "
            f"expected {expected_width}x{expected_height}"
        )


def _encode_sprite(
    image: RgbaImage,
    meta: SheetMeta,
    spec: SheetSpec,
    *,
    pose_index: int,
    palette_rgb565: Sequence[int],
    alpha_threshold: int,
) -> SpriteRust:
    pose = spec.poses[pose_index]
    cell_x = (pose_index % spec.columns) * meta.cell_width
    cell_y = (pose_index // spec.columns) * meta.cell_height
    min_x = meta.cell_width
    min_y = meta.cell_height
    max_x = -1
    max_y = -1
    visible_pixels = 0

    for y in range(meta.cell_height):
        for x in range(meta.cell_width):
            _r, _g, _b, alpha = image.pixel(cell_x + x, cell_y + y)
            if alpha >= alpha_threshold:
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
                visible_pixels += 1

    enum_name = _upper_camel(pose)
    if max_x < min_x or max_y < min_y:
        return SpriteRust(
            pose=pose,
            const_name=_const_name(pose),
            enum_name=enum_name,
            x=0,
            y=0,
            width=0,
            height=0,
            data=(),
            visible_pixels=0,
        )

    indices: list[int | None] = []
    for y in range(min_y, max_y + 1):
        for x in range(min_x, max_x + 1):
            r, g, b, alpha = image.pixel(cell_x + x, cell_y + y)
            if alpha < alpha_threshold:
                indices.append(None)
                continue
            rgb565 = _composite_to_rgb565(r, g, b, alpha)
            indices.append(_nearest_palette_index(rgb565, palette_rgb565))

    return SpriteRust(
        pose=pose,
        const_name=_const_name(pose),
        enum_name=enum_name,
        x=min_x,
        y=min_y,
        width=max_x - min_x + 1,
        height=max_y - min_y + 1,
        data=tuple(_encode_rle(indices)),
        visible_pixels=visible_pixels,
    )


def _encode_rle(indices: Sequence[int | None]) -> Iterable[int]:
    pos = 0
    while pos < len(indices):
        value = indices[pos]
        run_len = 1
        while (
            pos + run_len < len(indices)
            and run_len < MAX_RUN_LEN
            and indices[pos + run_len] == value
        ):
            run_len += 1
        if value is None:
            yield run_len - 1
        else:
            yield SOLID_RUN_FLAG | (run_len - 1)
            yield value
        pos += run_len


def _composite_to_rgb565(r: int, g: int, b: int, alpha: int) -> int:
    # The OLED background is black; precomposite antialiasing onto black so the
    # device does not need runtime alpha blending.
    out_r = (r * alpha + 127) // 255
    out_g = (g * alpha + 127) // 255
    out_b = (b * alpha + 127) // 255
    return ((out_r >> 3) << 11) | ((out_g >> 2) << 5) | (out_b >> 3)


def _nearest_palette_index(rgb565: int, palette_rgb565: Sequence[int]) -> int:
    target = _rgb565_components(rgb565)
    best_index = 0
    best_distance = 1 << 60
    for index, color in enumerate(palette_rgb565):
        candidate = _rgb565_components(color)
        dr = target[0] - candidate[0]
        dg = target[1] - candidate[1]
        db = target[2] - candidate[2]
        distance = dr * dr * 4 + dg * dg * 2 + db * db * 4
        if distance < best_distance:
            best_distance = distance
            best_index = index
    return best_index


def _rgb565_components(rgb565: int) -> tuple[int, int, int]:
    return (rgb565 >> 11) & 0x1F, (rgb565 >> 5) & 0x3F, rgb565 & 0x1F


def _const_name(pose: str) -> str:
    return "POSE_" + re.sub(r"[^A-Za-z0-9]+", "_", pose).strip("_").upper()


def _upper_camel(pose: str) -> str:
    parts = re.split(r"[^A-Za-z0-9]+", pose)
    return "".join(part[:1].upper() + part[1:] for part in parts if part)


def _format_int_list(values: Sequence[int], *, hex_width: int, indent: str) -> str:
    if not values:
        return ""
    formatted = [f"0x{value:0{hex_width}X}" for value in values]
    chunks = [
        ", ".join(formatted[index : index + 16])
        for index in range(0, len(formatted), 16)
    ]
    return "\n".join(f"{indent}{chunk}," for chunk in chunks)


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert Shiro's RGBA sprite sheet into no_std Rust RLE assets."
    )
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--alpha-threshold",
        type=int,
        default=DEFAULT_ALPHA_THRESHOLD,
        help="Pixels below this alpha are encoded as transparent.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if the generated Rust would differ from --output.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stats = convert_sheet(
        args.sheet,
        args.metadata,
        args.output,
        alpha_threshold=args.alpha_threshold,
        check=args.check,
    )
    action = "checked" if args.check else "wrote"
    print(
        f"{action} {stats.sprite_count} sprites, {stats.visible_pixels} visible pixels, "
        f"{stats.rle_bytes} RLE bytes, {stats.palette_size} colors"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
