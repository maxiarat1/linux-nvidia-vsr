"""Read the decoder codec string from NVENC's actual AV1 sequence header."""


class Bits:
    def __init__(self, data):
        self.data, self.offset = data, 0

    def read(self, count):
        if self.offset + count > len(self.data) * 8:
            raise ValueError("Truncated AV1 sequence header")
        value = 0
        for _ in range(count):
            value = value * 2 + (
                (self.data[self.offset // 8] >> (7 - self.offset % 8)) & 1
            )
            self.offset += 1
        return value


def codec_string(packet):
    offset = 0
    while offset < len(packet):
        header = packet[offset]
        offset += 1
        kind = (header >> 3) & 15
        if header & 4:
            offset += 1  # OBU extension header
        size = len(packet) - offset
        if header & 2:
            size = 0
            for shift in range(0, 56, 7):
                if offset >= len(packet):
                    raise ValueError("Truncated AV1 OBU size")
                byte = packet[offset]
                offset += 1
                size |= (byte & 127) << shift
                if not byte & 128:
                    break
            else:
                raise ValueError("Invalid AV1 OBU size")
        if offset + size > len(packet):
            raise ValueError("Truncated AV1 OBU")
        if kind == 1:
            bits = Bits(packet[offset : offset + size])
            profile = bits.read(3)
            bits.read(1)  # still_picture
            reduced = bits.read(1)
            if not reduced:
                if bits.read(1):  # timing_info_present_flag
                    bits.read(32)  # num_units_in_display_tick
                    bits.read(32)  # time_scale
                    if bits.read(1):  # equal_picture_interval -> UVLC
                        zeros = 0
                        while not bits.read(1):
                            zeros += 1
                            if zeros >= 32:
                                break
                        if zeros < 32:
                            bits.read(zeros)
                    if bits.read(1):  # decoder_model_info_present_flag
                        bits.read(5 + 32 + 5 + 5)
                bits.read(1)  # initial_display_delay_present_flag
                bits.read(5)  # operating_points_cnt_minus_1
                bits.read(12)  # first operating_point_idc
            level = bits.read(5)
            tier = bits.read(1) if not reduced and level > 7 else 0
            # The adapter is explicitly NV12 8-bit, profile 0; bit depth is
            # fixed by that encoder configuration rather than inferred here.
            if profile != 0:
                raise ValueError("Unexpected NVENC AV1 profile")
            return f"av01.{profile}.{level:02d}{'H' if tier else 'M'}.08"
        offset += size
    raise ValueError("NVENC packet has no AV1 sequence header")
