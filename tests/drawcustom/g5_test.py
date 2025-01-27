"""Tests for g5 image decoder."""
import os
from io import BytesIO
import pytest
from PIL import Image

from custom_components.open_epaper_link import g5

from conftest import BASE_IMG_PATH, images_equal, save_image

G5_IMG_PATH = os.path.join(BASE_IMG_PATH, 'g5')

@pytest.mark.asyncio
async def test_parser():
    expected_width = 128
    expected_height = 296

    expected_img = Image.open(os.path.join(G5_IMG_PATH, 'decoded.png'))
    assert expected_img.width == expected_width
    assert expected_img.height == expected_height

    raw_g5_data = open(os.path.join(G5_IMG_PATH, 'raw.g5'), 'rb').read()
    assert len(raw_g5_data) > 0

    decoded_bytes = g5.decode_g5_image(raw_g5_data, expected_width, expected_height)
    assert decoded_bytes is not None
    assert len(decoded_bytes) > 0
    decoded_img = Image.open(BytesIO(decoded_bytes))
    assert decoded_img.width == expected_width
    assert decoded_img.height == expected_height
