# M2 narrow None-flow: attribute access on a value that may be None.
def port_bits(code):
    return code.bit_length()


def safe_port_bits(code):
    if code is None:
        return 0
    return code.bit_length()
