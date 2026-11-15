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


def verify_reported_constant(code, expected, function_name, register_width):
    # Decode the AArch64 MOVZ/MOVK sequence used to initialize struct fields.
    registers = {}
    for offset in range(0, len(code) - 3, 4):
        word = struct.unpack_from("<I", code, offset)[0]
        if word & 0x1F800000 != 0x12800000:
            continue
        width = 64 if word >> 31 else 32
        if width != register_width:
            continue
        opc = (word >> 29) & 0x3
        hw = (word >> 21) & 0x3
        imm16 = (word >> 5) & 0xFFFF
        register = word & 0x1F
        if hw * 16 >= width:
            raise ElfError(f"{function_name}: invalid MOV-wide shift")
        full_mask = (1 << width) - 1
        if opc == 2:
            registers[register] = (imm16 << (hw * 16)) & full_mask
        elif opc == 3 and register in registers:
            mask = 0xFFFF << (hw * 16)
            registers[register] = ((registers[register] & ~mask) | (imm16 << (hw * 16))) & full_mask
        else:
            continue
        if registers[register] == expected:
            return offset
    raise ElfError(f"{function_name}: expected compiled version {expected} not found")


def verify_uapi_store(code, constant_offset, function_name):
    register = struct.unpack_from("<I", code, constant_offset)[0] & 0x1F
    for offset in range(constant_offset + 4, len(code) - 3, 4):
        word = struct.unpack_from("<I", code, offset)[0]
        # 64-bit STR (unsigned immediate), with the byte offset scaled by 8.
        if word & 0xFFC00000 != 0xF9000000 or word & 0x1F != register:
            continue
        if (word >> 5) & 0x1F != 31:
            continue
        stack_offset = ((word >> 10) & 0xFFF) * 8
        if stack_offset == 16:
            return offset
        break
    raise ElfError(f"{function_name}: uapi_version is not stored at GET_INFO offset 16")


def verify_version_store(code, constant_offset, function_name, stack_offset):
    register = struct.unpack_from("<I", code, constant_offset)[0] & 0x1F
    for offset in range(constant_offset + 4, len(code) - 3, 4):
        word = struct.unpack_from("<I", code, offset)[0]
        if (word & 0xFFC003E0) == 0xB90003E0 and (word & 0x1F) == register:
            if ((word >> 10) & 0xFFF) * 4 == stack_offset:
                return offset
        # STP Wt, Wt2, [SP, #imm] (signed, scaled pair offset).
        if word & 0xFFC00000 != 0x29000000 or (word >> 5) & 0x1F != 31:
            continue
        first_register = word & 0x1F
        second_register = (word >> 10) & 0x1F
        pair_offset = (word >> 15) & 0x7F
        if pair_offset & 0x40:
            pair_offset -= 0x80
        if first_register == register and pair_offset * 4 == stack_offset:
            return offset
        if second_register == register and pair_offset * 4 + 4 == stack_offset:
            return offset
    raise ElfError(f"{function_name}: version field store not found at SP+{stack_offset}")


def inspect_module(path, expected_version, expected_uapi):
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
        function_code = code[value : value + size]
        entry = {"version": expected_version, "section": section_names[index], "offset": hex(value)}
        version_constant_offset = verify_reported_constant(
            function_code, expected_version, name, 32
        )
        if name == "do_get_info_legacy":
            verify_version_store(function_code, version_constant_offset, name, 12)
        if name == "do_get_info":
            verify_version_store(function_code, version_constant_offset, name, 8)
            # Current GET_INFO stores { features, uapi_version } as one 64-bit value in x8.
            uapi_constant = expected_uapi * 2**32 + 7
            uapi_offset = verify_reported_constant(function_code, uapi_constant, name + ".uapi", 64)
            verify_uapi_store(function_code, uapi_offset, name)
            entry["uapi_version"] = expected_uapi
            entry["uapi_store_offset"] = 16
        found[name] = entry
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
        "reported_uapi_version": expected_uapi,
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
    parser.add_argument("--expected-uapi", type=int, default=8)
    parser.add_argument("--build-commit")
    parser.add_argument("--source-baseline")
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    if not 0 < args.expected_version <= 0xFFFFFFFF:
        parser.error("The expected version must be a positive 32-bit integer")
    if not 0 < args.expected_uapi <= 0xFFFF:
        parser.error("The UAPI version must be a positive 16-bit integer")
    try:
        result = inspect_module(args.module, args.expected_version, args.expected_uapi)
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
