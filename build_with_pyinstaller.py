"""Run PyInstaller with the CPython 3.10.0 dis workaround (bpo-45757)."""

import dis
import sys


def fix_legacy_dis():
    """Reset EXTENDED_ARG after argumentless opcodes on affected interpreters."""
    if sys.version_info[:2] != (3, 10):
        return False
    probe = bytes([dis.EXTENDED_ARG, 1, dis.opmap["NOP"], 0,
                   dis.opmap["LOAD_CONST"], 1])
    if list(dis._unpack_opargs(probe))[-1][2] == 1:
        return False

    # CPython bpo-45757: NOP must discard the preceding EXTENDED_ARG.
    # Scope this compatibility fix to this build process, never the Python install.
    def unpack_opargs(code):
        extended_arg = 0
        for offset in range(0, len(code), 2):
            op = code[offset]
            if op >= dis.HAVE_ARGUMENT:
                arg = code[offset + 1] | extended_arg
                extended_arg = arg << 8 if op == dis.EXTENDED_ARG else 0
            else:
                arg = None
                extended_arg = 0
            yield offset, op, arg

    dis._unpack_opargs = unpack_opargs
    return True


if __name__ == "__main__":
    if fix_legacy_dis():
        print("Applied Python 3.10 dis compatibility fix (bpo-45757).", flush=True)
    from PyInstaller.__main__ import run

    run()
