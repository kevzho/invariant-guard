def port_bits(code: int | None):
    if code is None:
        return 0
    return code.bit_length()
