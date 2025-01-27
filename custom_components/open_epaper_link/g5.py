import logging
import struct
from typing import Final

# ---------------------------
# Constants and enums
# ---------------------------


class G5DecodeError(Exception):
    pass


class G5InvalidParameterError(G5DecodeError):
    pass


_LOGGER: Final = logging.getLogger(__name__)

MAX_IMAGE_FLIPS = 640  # Max color changes per line

# Horizontal prefix bits
HORIZ_SHORT_SHORT = 0
HORIZ_SHORT_LONG = 1
HORIZ_LONG_SHORT = 2
HORIZ_LONG_LONG = 3

# Decoder return codes
G5_SUCCESS = 0
G5_INVALID_PARAMETER = 1
G5_DECODE_ERROR = 2
G5_UNSUPPORTED_FEATURE = 3
G5_ENCODE_COMPLETE = 4
G5_DECODE_COMPLETE = 5
G5_NOT_INITIALIZED = 6
G5_DATA_OVERFLOW = 7
G5_MAX_FLIPS_EXCEEDED = 8

REGISTER_WIDTH = 32  # Must be 32 bits

# ---------------------------
# The code_table from the C code
# ---------------------------
code_table = [
    0x90, 0, 0x40, 0,             # trash, uncompr mode - codes 0 and 1
    3, 7,                         # V(-3) pos = 2
    0x13, 7,                      # V(3)  pos = 3
    2, 6, 2, 6,                   # V(-2) pos = 4,5
    0x12, 6, 0x12, 6,             # V(2)  pos = 6,7
    0x30, 4, 0x30, 4, 0x30, 4, 0x30, 4,   # pass  pos = 8->F
    0x30, 4, 0x30, 4, 0x30, 4, 0x30, 4,
    0x20, 3, 0x20, 3, 0x20, 3, 0x20, 3,   # horiz pos = 10->1F
    0x20, 3, 0x20, 3, 0x20, 3, 0x20, 3,
    0x20, 3, 0x20, 3, 0x20, 3, 0x20, 3,
    0x20, 3, 0x20, 3, 0x20, 3, 0x20, 3,
    # V(-1) pos = 20->2F
    1, 3, 1, 3, 1, 3, 1, 3,
    1, 3, 1, 3, 1, 3, 1, 3,
    1, 3, 1, 3, 1, 3, 1, 3,
    1, 3, 1, 3, 1, 3, 1, 3,
    # V(1) pos = 30->3F
    0x11, 3, 0x11, 3, 0x11, 3, 0x11, 3,
    0x11, 3, 0x11, 3, 0x11, 3, 0x11, 3,
    0x11, 3, 0x11, 3, 0x11, 3, 0x11, 3,
    0x11, 3, 0x11, 3, 0x11, 3, 0x11, 3
]  # fmt: skip

# ---------------------------
# Utility functions
# ---------------------------


def tiff_moto_long(data: bytes, offset: int) -> int:
    """
    Equivalent to the TIFFMOTOLONG macro in the C code:
    read a big-endian 32-bit integer from data[offset..offset+3].
    """
    # Make sure we don't go out of range
    try:
        (val,) = struct.unpack_from(">i", data, offset)
        return val
    except struct.error as e:
        _LOGGER.warning(e, "Could not decode 4 bytes from g5 buffer")
        return 0


# ---------------------------
# Decoder data structure
# ---------------------------


class G5DECIMAGE:
    def __init__(self):
        self.i_width = 0
        self.i_height = 0
        self.i_error = 0
        self.y = 0
        self.i_vlc_size = 0
        self.i_h_len = 0
        self.i_pitch = 0
        self.u32_accum = 0
        self.ul_bit_off = 0
        self.ul_bits = 0
        self.p_src = None  # Input buffer (bytes)
        self.p_buf = None  # Current position in input
        self.p_buf_index = 0  # Current offset in p_buf
        # Our "flips" arrays
        self.p_cur = [0] * MAX_IMAGE_FLIPS
        self.p_ref = [0] * MAX_IMAGE_FLIPS


# ---------------------------
# G5 decode init (equivalent to g5_decode_init in C)
# ---------------------------
def g5_decode_init(p_image: G5DECIMAGE, width: int, height: int, data: bytes):
    if p_image is None or width < 1 or height < 1 or data is None or len(data) < 1:
        raise G5InvalidParameterError("Invalid input to g5_decode_init")

    p_image.i_width = width
    p_image.i_height = height
    p_image.i_vlc_size = len(data)
    p_image.p_src = data
    p_image.p_buf = data
    p_image.p_buf_index = 0
    p_image.ul_bit_off = 0
    p_image.y = 0
    # Preload the first 32 bits
    p_image.ul_bits = tiff_moto_long(data, 0)


# ---------------------------
# G5DrawLine (equivalent to the C function)
#   Convert flip runs to actual black bits in p_out (1-bpp).
# ---------------------------
def g5_draw_line(p_page: G5DECIMAGE, p_cur_flips, p_out):
    xright = p_page.i_width
    length = (xright + 7) >> 3

    # Fill row with white
    for i in range(length):
        p_out[i] = 0xFF

    cur_idx = 0
    while True:
        # Make sure we have two flips (start/end)
        if cur_idx + 1 >= len(p_cur_flips):
            break

        xstart = p_cur_flips[cur_idx]
        cur_idx += 1
        xend = p_cur_flips[cur_idx]
        cur_idx += 1

        run = xend - xstart
        if xstart >= xright or run <= 0:
            # No valid run; done or skip
            break

        # --- 1) Clip if (xstart + run) > xright ---
        if (xstart + run) > xright:
            run = xright - xstart
        if run <= 0:
            continue

        # --- 2) The last pixel is (xstart + run - 1) ---
        end_pixel = xstart + run - 1

        # Compute byte indices
        left_byte_index = xstart >> 3
        right_byte_index = end_pixel >> 3

        # Build bit masks:
        #  - left mask zeroes bits before xstart
        lmask = (0xFF << (8 - (xstart & 7))) & 0xFF
        #  - right mask zeroes bits after end_pixel
        rmask = (0xFF >> ((end_pixel + 1) & 7)) & 0xFF

        if left_byte_index == right_byte_index:
            # Run is contained in one byte
            combined_mask = (lmask | rmask) & 0xFF
            p_out[left_byte_index] &= combined_mask
        else:
            # Mask the left-most byte
            p_out[left_byte_index] &= lmask

            # Zero out the fully covered bytes
            for b in range(left_byte_index + 1, right_byte_index):
                p_out[b] = 0x00

            # Mask the right-most byte (ensure we don't go beyond p_out)
            if right_byte_index < length:
                p_out[right_byte_index] &= rmask
    # End of while


# ---------------------------
# Decode_Begin (equivalent to the C "Decode_Begin")
#   Called the first time we decode a line
# ---------------------------
def decode_begin(p_page):
    xsize = p_page.i_width

    # Seed the current and reference lines with xsize
    for i in range(MAX_IMAGE_FLIPS - 2):
        p_page.p_ref[i] = xsize
        p_page.p_cur[i] = xsize

    # Fill with 0x7FFF near the end
    p_page.p_ref[MAX_IMAGE_FLIPS - 2] = 0x7FFF
    p_page.p_ref[MAX_IMAGE_FLIPS - 1] = 0x7FFF
    p_page.p_cur[MAX_IMAGE_FLIPS - 2] = 0x7FFF
    p_page.p_cur[MAX_IMAGE_FLIPS - 1] = 0x7FFF

    # Re-load 32 bits
    p_page.p_buf_index = 0
    p_page.ul_bits = tiff_moto_long(p_page.p_buf, 0)
    p_page.ul_bit_off = 0

    # i_h_len = number of bits needed to encode a "long" horizontal code
    p_page.i_h_len = xsize.bit_length()


# ---------------------------
# DecodeLine (equivalent to the C function)
#   Decodes flips for one line
# ---------------------------
def decode_line(p_page: G5DECIMAGE) -> int:
    p_cur = p_page.p_cur
    p_ref = p_page.p_ref
    width = p_page.i_width

    ul_bits = p_page.ul_bits
    ul_bit_off = p_page.ul_bit_off
    buf_index = p_page.p_buf_index

    u32_h_len = p_page.i_h_len
    u32_h_mask = (1 << u32_h_len) - 1

    cur_idx = 0
    ref_idx = 0
    a0 = -1

    _LOGGER.info(
        "Start decode line y=%s, width=%s, buf_index=%s, ul_bits=0x%x ul_bit_off=%s",
        p_page.y,
        width,
        buf_index,
        ul_bits,
        ul_bit_off,
    )

    while a0 < width:
        # If we don't have room to shift bits properly, reload
        if ul_bit_off > (REGISTER_WIDTH - 8):
            shift_bytes = ul_bit_off >> 3
            buf_index += shift_bytes
            ul_bit_off &= 7
            ul_bits = tiff_moto_long(p_page.p_src, buf_index)
            _LOGGER.info(
                "Reload 32 bits at buf_index=%s, ul_bits=0x%x", buf_index, ul_bits
            )

        # Check top bit => "if ((ul_bits << ul_bit_off) & 0x80000000) != 0"
        if ((ul_bits << ul_bit_off) & 0x80000000) != 0:
            a0 = p_ref[ref_idx]
            ref_idx += 1
            p_cur[cur_idx] = a0
            cur_idx += 1
            ul_bit_off += 1
            # Debug:
            _LOGGER.info("V(0) => a0=%s, ref_idx=%s, cur_idx=%s", a0, ref_idx, cur_idx)
        else:
            # Next 7 bits => used as an index in code_table
            l_bits = (ul_bits >> (REGISTER_WIDTH - 8 - ul_bit_off)) & 0xFE
            if l_bits >= len(code_table):
                _LOGGER.info("l_bits=%s out-of-range for code_table!", l_bits)
                p_page.i_error = G5_DECODE_ERROR
                break

            s_code = code_table[l_bits]
            bit_len = code_table[l_bits + 1]
            ul_bit_off += bit_len

            _LOGGER.info(
                "code=0x%x, bit_len=%s, l_bits=%s, ref_idx=%s, cur_idx=%s, a0=%s",
                s_code,
                bit_len,
                l_bits,
                ref_idx,
                cur_idx,
                a0,
            )

            if s_code in (1, 2, 3):  # V(-1), V(-2), V(-3)
                if ref_idx >= MAX_IMAGE_FLIPS:
                    _LOGGER.info(
                        "ref_idx=%s out-of-range before applying s_code=%s",
                        ref_idx,
                        s_code,
                    )
                    p_page.i_error = G5_DECODE_ERROR
                    break
                a0 = p_ref[ref_idx] - s_code
                p_cur[cur_idx] = a0
                cur_idx += 1
                if ref_idx == 0:
                    ref_idx += 2
                ref_idx -= 1
                # Move p_ref until a0 < p_ref[ref_idx]
                while a0 >= p_ref[ref_idx]:
                    ref_idx += 2
                    if ref_idx >= MAX_IMAGE_FLIPS:
                        _LOGGER.info(
                            "ref_idx out-of-range in loop for vertical negative code"
                        )
                        p_page.i_error = G5_DECODE_ERROR
                        break
                _LOGGER.info(
                    "V(-%s): new a0=%s, ref_idx=%s, cur_idx=%s",
                    s_code,
                    a0,
                    ref_idx,
                    cur_idx,
                )

            elif s_code in (0x11, 0x12, 0x13):  # V(1), V(2), V(3)
                if ref_idx >= MAX_IMAGE_FLIPS:
                    _LOGGER.info(
                        "ref_idx=%s out-of-range for s_code=0x%x", ref_idx, s_code
                    )
                    p_page.i_error = G5_DECODE_ERROR
                    break
                b1 = p_ref[ref_idx]
                ref_idx += 1
                a0 = b1 + (s_code & 7)
                if b1 != width and a0 < width:
                    while a0 >= p_ref[ref_idx]:
                        ref_idx += 2
                        if ref_idx >= MAX_IMAGE_FLIPS:
                            _LOGGER.info(
                                "ref_idx out-of-range in loop for vertical positive code"
                            )
                            p_page.i_error = G5_DECODE_ERROR
                            break
                if a0 > width:
                    a0 = width
                p_cur[cur_idx] = a0
                cur_idx += 1
                _LOGGER.info(
                    "V(+%s): b1=%s, a0=%s, ref_idx=%s, cur_idx=%s",
                    s_code & 7,
                    b1,
                    a0,
                    ref_idx,
                    cur_idx,
                )

            elif s_code == 0x20:  # Horizontal
                # Possibly reload bits
                if ul_bit_off > (REGISTER_WIDTH - 16):
                    shift_bytes = ul_bit_off >> 3
                    buf_index += shift_bytes
                    ul_bit_off &= 7
                    ul_bits = tiff_moto_long(p_page.p_src, buf_index)

                a0_p = max(0, a0)
                prefix = (ul_bits >> ((REGISTER_WIDTH - 2) - ul_bit_off)) & 0x3
                ul_bit_off += 2

                # Depending on prefix, read short or long runs
                if prefix == HORIZ_SHORT_SHORT:
                    tot_run = (ul_bits >> ((REGISTER_WIDTH - 3) - ul_bit_off)) & 0x7
                    ul_bit_off += 3
                    tot_run1 = (ul_bits >> ((REGISTER_WIDTH - 3) - ul_bit_off)) & 0x7
                    ul_bit_off += 3
                    debug_prefix = "SHORT_SHORT"
                elif prefix == HORIZ_SHORT_LONG:
                    tot_run = (ul_bits >> ((REGISTER_WIDTH - 3) - ul_bit_off)) & 0x7
                    ul_bit_off += 3
                    tot_run1 = (
                        ul_bits >> ((REGISTER_WIDTH - u32_h_len) - ul_bit_off)
                    ) & u32_h_mask
                    ul_bit_off += u32_h_len
                    debug_prefix = "SHORT_LONG"
                elif prefix == HORIZ_LONG_SHORT:
                    tot_run = (
                        ul_bits >> ((REGISTER_WIDTH - u32_h_len) - ul_bit_off)
                    ) & u32_h_mask
                    ul_bit_off += u32_h_len
                    tot_run1 = (ul_bits >> ((REGISTER_WIDTH - 3) - ul_bit_off)) & 0x7
                    ul_bit_off += 3
                    debug_prefix = "LONG_SHORT"
                else:  # HORIZ_LONG_LONG
                    tot_run = (
                        ul_bits >> ((REGISTER_WIDTH - u32_h_len) - ul_bit_off)
                    ) & u32_h_mask
                    ul_bit_off += u32_h_len
                    debug_prefix = "LONG_LONG"
                    if ul_bit_off > (REGISTER_WIDTH - 16):
                        shift_bytes = ul_bit_off >> 3
                        buf_index += shift_bytes
                        ul_bit_off &= 7
                        ul_bits = tiff_moto_long(p_page.p_src, buf_index)
                    tot_run1 = (
                        ul_bits >> ((REGISTER_WIDTH - u32_h_len) - ul_bit_off)
                    ) & u32_h_mask
                    ul_bit_off += u32_h_len

                a0 = a0_p + tot_run
                p_cur[cur_idx] = a0
                cur_idx += 1
                a0 += tot_run1
                if a0 < width:
                    while a0 >= p_ref[ref_idx]:
                        ref_idx += 2
                        if ref_idx >= MAX_IMAGE_FLIPS:
                            _LOGGER.info(
                                "ref_idx out-of-range in horizontal run, a0=%s", a0
                            )
                            p_page.i_error = G5_DECODE_ERROR
                            break
                p_cur[cur_idx] = a0
                cur_idx += 1

                _LOGGER.info(
                    "HORIZ %s => tot_run=%s, tot_run1=%s, a0=%s, ref_idx=%s, "
                    "cur_idx=%s",
                    debug_prefix,
                    tot_run,
                    tot_run1,
                    a0,
                    ref_idx,
                    cur_idx,
                )

            elif s_code == 0x30:  # Pass code
                ref_idx += 1
                if ref_idx >= MAX_IMAGE_FLIPS:
                    _LOGGER.info("ref_idx out-of-range in pass code")
                    p_page.i_error = G5_DECODE_ERROR
                    break
                a0 = p_ref[ref_idx]
                ref_idx += 1
                _LOGGER.info("PASS => a0=%s, ref_idx=%s", a0, ref_idx)

            else:
                # ERROR
                _LOGGER.info(
                    "Unknown or invalid s_code=0x%x, l_bits=%s", s_code, l_bits
                )
                p_page.i_error = G5_DECODE_ERROR
                break

        if p_page.i_error == G5_DECODE_ERROR:
            break

    # Terminate the line
    if p_page.i_error == G5_SUCCESS:
        p_cur[cur_idx] = width
        cur_idx += 1
        p_cur[cur_idx] = width
        cur_idx += 1

    # Save back the decode state
    p_page.ul_bits = ul_bits
    p_page.ul_bit_off = ul_bit_off
    p_page.p_buf_index = buf_index

    if p_page.i_error == G5_SUCCESS:
        _LOGGER.info(
            "Line y=%s decode success. cur_idx=%s, final a0=%s", p_page.y, cur_idx, a0
        )
    else:
        _LOGGER.info("Line y=%s decode error=%s", p_page.y, p_page.i_error)

    _LOGGER.info(
        "End of line %s, next pBufIndex=%s, totalSize=%s",
        p_page.y,
        p_page.p_buf_index,
        p_page.i_vlc_size,
    )
    return p_page.i_error


# ---------------------------
# g5_decode_line (the public function in C).
# ---------------------------
def g5_decode_line(p_page: G5DECIMAGE, p_out):
    """
    Decodes the next line of the G5 image into p_out (bytearray of size (width+7)//8).
    Returns G5_SUCCESS, G5_DECODE_COMPLETE, or an error code.
    """
    if p_page is None or p_out is None:
        raise G5InvalidParameterError("g5_decode_line invalid input")

    if p_page.y >= p_page.i_height:
        return G5_DECODE_COMPLETE  # Already done

    if p_page.p_buf_index >= p_page.i_vlc_size:
        p_page.i_error = G5_DECODE_ERROR
        return G5_DECODE_ERROR

    rc = decode_line(p_page)
    if rc == G5_SUCCESS:
        g5_draw_line(p_page, p_page.p_cur, p_out)
        # Swap p_cur/p_ref
        temp = p_page.p_ref
        p_page.pRef = p_page.p_cur
        p_page.pCur = temp
        p_page.y += 1

        if p_page.y >= p_page.i_height:
            p_page.iError = G5_DECODE_COMPLETE
            return G5_DECODE_COMPLETE
        else:
            return G5_SUCCESS
    else:
        return rc


# ---------------------------
# A "higher-level" decode function that does all lines
# and returns a bytes object of the entire image.
# ---------------------------
def decode_g5_image(data: bytes, width: int, height: int) -> bytes | None:
    """
    Decodes the entire G5 data (in 'data') to a 1-bpp image of size (width x height).
    Returns a bytes object of length ( (width+7)//8 * height ), or None on error.
    """
    p_image = G5DECIMAGE()
    g5_decode_init(p_image, width, height, data)

    decode_begin(p_image)

    bytes_per_line = (width + 7) >> 3
    total_bytes = bytes_per_line * height
    out_buf = bytearray(total_bytes)

    for y in range(height):
        line_start = y * bytes_per_line
        line_end = line_start + bytes_per_line
        row = memoryview(out_buf)
        row = row[line_start:line_end]  # subarray for this row

        rc_line = g5_decode_line(p_image, row)
        _LOGGER.info("Line: %s", row.hex())

        if rc_line not in [G5_SUCCESS, G5_DECODE_COMPLETE]:
            _LOGGER.info("Error decoding line %s, code=%s", y, rc_line)
            return None
        if rc_line == G5_DECODE_COMPLETE:
            break

    return bytes(out_buf)
