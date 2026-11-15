#!/usr/bin/env python3
"""Check the compiled AArch64 GET_INFO version without executing the module."""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys


class ElfError(ValueError):
    pass


def inspect_module(path, expected_version):
    data = path.read_bytes()
    if len(data) < 64 or data[:6] != b"\x7fELF\x02\x01":
        raise ElfError("Expected a little-endian ELF64 module")
    elf_type, machine = struct.unpack_from("<HH", data, 16)
    if elf_type != 1 or machine != 183:
        raise ElfError("Expected an AArch64 relocatable kernel module")

    shoff = struct.unpack_from("<Q", data, 40)[0]
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 58)
    if shentsize != 64 or shnum == 0 or shoff + shnum * shentsize > len(data):
        raise ElfError("Invalid or unsupported ELF section table")
    sections = [
        struct.unpack_from("<IIQQQQIIQQ", data, shoff + index * shentsize)
        for index in range(shnum)
    ]
    if shstrndx >= len(sections):
        raise ElfError("Invalid section-name table")

    def section_bytes(index):
        section = sections[index]
        offset, size = section[4:6]
        if offset + size > len(data):
            raise ElfError("Section extends outside the file")
        return data[offset : offset + size]

    def cstring(table, offset):
        if offset >= len(table):
            raise ElfError("Invalid string-table offset")
        end = table.find(b"\0", offset)
        if end < 0:
            raise ElfError("Unterminated ELF string")
        return table[offset:end].decode("utf-8", "replace")

    names = section_bytes(shstrndx)
    section_names = [cstring(names, section[0]) for section in sections]
    try:
        symidx = next(index for index, section in enumerate(sections) if section[1] == 2)
        modinfo_idx = section_names.index(".modinfo")
    except (StopIteration, ValueError) as error:
        raise ElfError("The module must retain .symtab and .modinfo") from error
    symtab = sections[symidx]
    if symtab[9] != 24 or symtab[6] >= len(sections):
        raise ElfError("Invalid symbol table")
    strings = section_bytes(symtab[6])
    symbol_data = section_bytes(symidx)
    wanted = {"do_get_info", "do_get_info_legacy"}
    found = {}
    for offset in range(0, len(symbol_data), 24):
        name_offset, info, _, index, value, size = struct.unpack_from("<IBBHQQ", symbol_data, offset)
        name = cstring(strings, name_offset)
        if name not in wanted or info & 0xF != 2:
            continue
        if index >= len(sections) or not size or size % 4:
            raise ElfError("Invalid version-handler function")
        code = section_bytes(index)
        if value + size > len(code):
            raise ElfError("Version handler extends outside its section")
        constants = []
        for instruction_offset in range(value, value + size, 4):
            instruction = struct.unpack_from("<I", code, instruction_offset)[0]
            # AArch64 MOVZ Wd, #imm16, LSL #shift (MOV is its usual alias).
            if instruction & 0xFF800000 == 0x52800000:
                shift = ((instruction >> 21) & 3) * 16
                if shift < 32:
                    constants.append(((instruction >> 5) & 0xFFFF) << shift)
        if expected_version not in constants:
            raise ElfError(
                f"{name}: expected version {expected_version} not found; MOVZ constants={constants}"
            )
        found[name] = {"version": expected_version, "section": section_names[index], "offset": hex(value)}
    if set(found) != wanted:
        raise ElfError("Both GET_INFO handlers must contain the expected compiled version")

    modinfo = {}
    for item in section_bytes(modinfo_idx).split(b"\0"):
        key, separator, value = item.decode("utf-8", "replace").partition("=")
        if separator:
            modinfo.setdefault(key, []).append(value)
    vermagic = modinfo.get("vermagic", [""])[0]
    if not vermagic.startswith("6.18.") or "aarch64" not in vermagic:
        raise ElfError(f"Unexpected vermagic: {vermagic}")
    return {
        "file": path.name,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "version_verified": True,
        "reported_version": expected_version,
        "version_handlers": found,
        "architecture": "aarch64",
        "target_kmi": "android17-6.18",
        "vermagic": vermagic,
        "device_tested": False,
        "scope": "Static version and ELF verification only; not a target-device ABI or runtime test",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("module", type=Path)
    parser.add_argument("--expected-version", type=int, default=32888)
    parser.add_argument("--build-commit")
    parser.add_argument("--source-baseline")
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    if not 0 < args.expected_version <= 0xFFFF:
        parser.error("This verifier supports positive 16-bit custom versions only")
    try:
        result = inspect_module(args.module, args.expected_version)
    except (OSError, ValueError, struct.error) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    if args.source_baseline:
        result["declared_upstream_baseline"] = args.source_baseline
    if args.build_commit:
        result["build_commit"] = args.build_commit
    report = json.dumps(result, indent=2, ensure_ascii=True) + "\n"
    if args.json_output:
        args.json_output.write_text(report, encoding="utf-8")
    print(report, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
