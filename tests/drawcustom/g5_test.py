"""Tests for g5 image decoder."""
import os
import pytest
import struct
from PIL import Image

from custom_components.open_epaper_link import g5
from custom_components.open_epaper_link import image_decompressor
from custom_components.open_epaper_link import tag_types

from conftest import BASE_IMG_PATH, images_equal

G5_IMG_PATH = os.path.join(BASE_IMG_PATH, "g5")

TAGTYPE_DICT = {
    "version": 4,
    "name": 'M2 2.9"',
    "width": 296,
    "height": 128,
    "rotatebuffer": 1,
    "bpp": 2,
    "colortable": {"white": [255, 255, 255], "black": [0, 0, 0], "red": [255, 0, 0]},
    "g5_compression": "29",
    "shortlut": 2,
    "options": ["button", "customlut"],
    "contentids": [22, 23, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 16, 17, 18, 19, 20, 21, 27],
    "template": {
        "1": {
            "weekday": [148, -3, "Signika-SB.ttf", 60],
            "date": [148, 65, "Signika-SB.ttf", 48],
        },
        "2": {
            "fonts": ["Signika-SB.ttf", 150, 150, 150, 120, 100, 80],
            "xy": [148, 53],
        },
        "16": {
            "location": [5, 5, "fonts/bahnschrift30"],
            "title": [247, 11, "glasstown_nbp_tf"],
            "cols": [1, 125, 12, "glasstown_nbp_tf"],
            "bars": [5, 111, 10],
        },
        "4": {
            "location": [5, 5, "fonts/bahnschrift30"],
            "wind": [280, 5, "fonts/bahnschrift30"],
            "temp": [5, 65, "fonts/bahnschrift70"],
            "icon": [285, 20, 70, 2],
            "dir": [235, -12, 40],
            "umbrella": [190, -50, 25],
        },
        "8": {
            "location": [5, 12, "t0_14b_tf"],
            "column": [5, 59],
            "day": [30, 18, "fonts/twcondensed20", 41, 108],
            "icon": [30, 55, 30],
            "wind": [18, 26],
            "line": [20, 128],
        },
        "9": {
            "title": [2, 0, "bahnschrift20.vlw", 25],
            "items": 8,
            "line": [1, 25, "REFSAN12.vlw"],
            "desc": [0, 5, "", 1],
        },
        "10": {"title": [149, 5, "fonts/bahnschrift20"], "pos": [149, 27]},
        "11": {
            "mode": 0,
            "days": 1,
            "title": [5, 2, "fonts/bahnschrift20"],
            "date": [290, 2],
            "items": 7,
            "red": [0, 21, 296, 14],
            "line": [5, 32, 15, "t0_14b_tf", 50],
        },
        "21": [
            {"text": [5, 5, "OpenEpaperLink AP", "bahnschrift20", 1, 0, 0]},
            {"text": [5, 50, "IP address:", "t0_14b_tf", 1, 0, 0]},
            {"text": [120, 50, "{ap_ip}", "t0_14b_tf", 1, 0, 0]},
            {"text": [5, 70, "Channel:", "t0_14b_tf", 1, 0, 0]},
            {"text": [120, 70, "{ap_ch}", "t0_14b_tf", 1, 0, 0]},
            {"text": [5, 90, "Tag count:", "t0_14b_tf", 1, 0, 0]},
            {"text": [120, 90, "{ap_tagcount}", "t0_14b_tf", 1, 0, 0]},
        ],
        "27": {
            "bars": [9, 288, 90, 11],
            "time": ["tahoma9.vlw"],
            "yaxis": ["tahoma9.vlw", 0, 6],
            "head": ["calibrib30.vlw"],
        },
    },
}


@pytest.mark.asyncio
async def test_parser():
    expected_width = 296
    expected_height = 128

    expected_img = Image.open(os.path.join(G5_IMG_PATH, "expected.png"))
    assert expected_img.width == expected_width
    assert expected_img.height == expected_height

    raw_g5_data = open(os.path.join(G5_IMG_PATH, "raw.g5"), "rb").read()
    assert len(raw_g5_data) > 0

    header_size = struct.unpack_from("B", raw_g5_data, 0)[0]
    assert header_size == 6
    bufw = struct.unpack_from("<H", raw_g5_data, 1)[0]
    assert bufw == expected_height
    bufh = struct.unpack_from("<H", raw_g5_data, 3)[0]
    assert bufh == expected_width

    print(f"header size: {header_size}")
    print(f"bufw: {bufw}")
    print(f"bufh: {bufh}")

    decoded_bytes = g5.decode_g5_image(raw_g5_data[header_size:], bufw, bufh)
    assert decoded_bytes is not None
    assert len(decoded_bytes) > 0

    tag_type = tag_types.TagType.from_dict(TAGTYPE_DICT)
    img = image_decompressor.to_image(decoded_bytes, tag_type)

    assert img is not None
    assert img.height == expected_height
    assert img.width == expected_width

    # For debugging.
    # img.save(os.path.join(G5_IMG_PATH, "decoded.png"), format="PNG")
    assert images_equal(img, expected_img), "Decoded g5 "
