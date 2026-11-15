#!/usr/bin/env python3
"""Verify a paired KernelSU Manager APK and its Gradle build metadata."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import zipfile


class ApkError(ValueError):
    pass


def _length8(data, offset):
    first = data[offset]
    if first & 0x80:
        return ((first & 0x7F) << 8) | data[offset + 1], offset + 2
    return first, offset + 1


def _length16(data, offset):
    first = struct.unpack_from("<H", data, offset)[0]
    if first & 0x8000:
        second = struct.unpack_from("<H", data, offset + 2)[0]
        return ((first & 0x7FFF) << 16) | second, offset + 4
    return first, offset + 2


def parse_string_pool(data, chunk_offset, header_size, chunk_size):
    string_count, style_count, flags, strings_start, _ = struct.unpack_from(
        "<IIIII", data, chunk_offset + 8
    )
    if string_count > 100000:
        raise ApkError("Unreasonable Android string-pool size")
    offsets = struct.unpack_from(f"<{string_count}I", data, chunk_offset + header_size)
    utf8 = bool(flags & 0x100)
    pool_start = chunk_offset + strings_start
    pool_end = chunk_offset + chunk_size
    values = []
    for relative in offsets:
        position = pool_start + relative
        if position >= pool_end:
            raise ApkError("Android string extends past its string pool")
        if utf8:
            _, position = _length8(data, position)
            byte_length, position = _length8(data, position)
            end = position + byte_length
            if end >= pool_end or data[end] != 0:
                raise ApkError("Malformed UTF-8 Android string")
            values.append(data[position:end].decode("utf-8", "replace"))
        else:
            char_length, position = _length16(data, position)
            end = position + char_length * 2
            if end + 2 > pool_end or data[end : end + 2] != b"\0\0":
                raise ApkError("Malformed UTF-16 Android string")
            values.append(data[position:end].decode("utf-16le", "replace"))
    return values


def _pool_value(strings, index):
    if index == 0xFFFFFFFF:
        return None
    if index >= len(strings):
        raise ApkError("Invalid Android string-pool reference")
    return strings[index]


def parse_manifest_axml(data):
    if len(data) < 8:
        raise ApkError("AndroidManifest.xml is too short")
    root_type, root_header, root_size = struct.unpack_from("<HHI", data, 0)
    if root_type != 0x0003 or root_size > len(data) or root_header < 8:
        raise ApkError("AndroidManifest.xml is not a valid binary XML document")

    strings = []
    manifest = None
    position = root_header
    while position + 8 <= root_size:
        chunk_type, header_size, chunk_size = struct.unpack_from("<HHI", data, position)
        if header_size < 8 or chunk_size < header_size or position + chunk_size > root_size:
            raise ApkError("Malformed Android binary XML chunk")
        if chunk_type == 0x0001:
            strings = parse_string_pool(data, position, header_size, chunk_size)
        elif chunk_type == 0x0102 and strings:
            if chunk_size < 36:
                raise ApkError("Malformed start-element chunk")
            node_name = _pool_value(strings, struct.unpack_from("<I", data, position + 20)[0])
            if node_name == "manifest":
                attribute_start, attribute_size, attribute_count = struct.unpack_from(
                    "<HHH", data, position + 24
                )
                if attribute_size < 20 or attribute_count > 256:
                    raise ApkError("Malformed manifest attribute table")
                attributes_position = position + 16 + attribute_start
                if attributes_position + attribute_count * attribute_size > position + chunk_size:
                    raise ApkError("Manifest attribute table exceeds its chunk")
                attributes = {}
                for index in range(attribute_count):
                    attribute_position = attributes_position + index * attribute_size
                    _, name_index, raw_value, typed_size, _, data_type, value = struct.unpack_from(
                        "<IIIHBBI", data, attribute_position
                    )
                    name = _pool_value(strings, name_index)
                    if not name:
                        continue
                    if data_type == 0x03:
                        parsed_value = _pool_value(strings, value)
                    elif data_type in (0x10, 0x11):
                        parsed_value = value
                    else:
                        parsed_value = _pool_value(strings, raw_value)
                    attributes[name] = parsed_value
                manifest = attributes
                break
        position += chunk_size

    if manifest is None:
        raise ApkError("Manifest element was not found")
    if not isinstance(manifest.get("versionCode"), int):
        raise ApkError("Manifest versionCode is missing or not an integer")
    return {
        "package": manifest.get("package"),
        "version_code": manifest["versionCode"],
        "version_name": manifest.get("versionName"),
        "version_code_major": manifest.get("versionCodeMajor", 0),
    }


def parse_gradle_metadata(path):
    metadata = json.loads(path.read_text(encoding="utf-8"))
    elements = metadata.get("elements")
    if not isinstance(elements, list) or len(elements) != 1:
        raise ApkError("Expected a single Gradle APK output")
    element = elements[0]
    return {
        "package": metadata.get("applicationId"),
        "version_code": element.get("versionCode"),
        "version_name": element.get("versionName"),
        "output_file": element.get("outputFile"),
    }


def parse_build_info(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _apksigner_path():
    found = shutil.which("apksigner")
    if found:
        return found
    sdk_roots = [os.environ.get("ANDROID_SDK_ROOT"), os.environ.get("ANDROID_HOME")]
    for root in filter(None, sdk_roots):
        candidates = sorted((Path(root) / "build-tools").glob("*/apksigner*"), reverse=True)
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    return None


def certificate_sha256(apk_path):
    signer = _apksigner_path()
    if not signer:
        raise ApkError("apksigner is required to verify the paired APK certificate")
    result = subprocess.run(
        [signer, "verify", "--print-certs", str(apk_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise ApkError(f"apksigner failed: {result.stderr.strip() or result.stdout.strip()}")
    matches = re.findall(
        r"certificate SHA-256 digest:\s*([0-9a-fA-F:]+)",
        result.stdout + chr(10) + result.stderr,
        flags=re.IGNORECASE,
    )
    fingerprints = {value.replace(":", "").lower() for value in matches}
    if len(fingerprints) != 1 or len(next(iter(fingerprints))) != 64:
        raise ApkError("apksigner did not report a signer SHA-256 fingerprint")
    return fingerprints.pop()


def manager_uapi_from_jni(data):
    """Read the constant-return JNI export produced by the pinned ARM64 build."""
    if data[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", data, 18)[0] != 183:
        raise ApkError("Manager JNI is not an ARM64 ELF64 library")
    shoff = struct.unpack_from("<Q", data, 40)[0]
    entsize, count = struct.unpack_from("<HH", data, 58)
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, shoff + i * entsize) for i in range(count)]
    for section in sections:
        if section[1] != 11:
            continue
        strings = sections[section[6]]
        names = data[strings[4]:strings[4] + strings[5]]
        for offset in range(section[4], section[4] + section[5], section[9]):
            no, info, _, idx, value, size = struct.unpack_from("<IBBHQQ", data, offset)
            name = names[no:names.index(b"\0", no)]
            if name != b"Java_me_weishu_kernelsu_Natives_getManagerUAPIVersion":
                continue
            if idx >= len(sections) or info & 15 != 2:
                raise ApkError("Invalid UAPI JNI symbol")
            code = sections[idx]
            start = code[4] + value - code[3]
            if size != 8:
                raise ApkError("Unknown UAPI JNI code layout; inspect it before accepting")
            move, ret = struct.unpack_from("<II", data, start)
            if move & 0xFFE0001F != 0x52800000 or ret != 0xD65F03C0:
                raise ApkError("UAPI JNI must be MOV W0, immediate; RET")
            return (move >> 5) & 0xFFFF
    raise ApkError("Manager UAPI JNI export was not found")


def verify(apk_path, gradle_metadata_path, build_info_path, expected_package,
           expected_version_code, expected_version_name, expected_uapi,
           expected_signer_sha256=None):
    apk_path = Path(apk_path)
    try:
        with zipfile.ZipFile(apk_path) as apk:
            manifest_xml = apk.read("AndroidManifest.xml")
            native_uapi = manager_uapi_from_jni(apk.read("lib/arm64-v8a/libkernelsu.so"))
    except (OSError, KeyError, zipfile.BadZipFile) as error:
        raise ApkError(f"Cannot read APK AndroidManifest.xml: {error}") from error

    manifest = parse_manifest_axml(manifest_xml)
    if native_uapi != expected_uapi:
        raise ApkError(f"Compiled Manager JNI UAPI {native_uapi}, expected {expected_uapi}")
    gradle = parse_gradle_metadata(Path(gradle_metadata_path))
    build_info = parse_build_info(Path(build_info_path))
    expected = {
        "package": expected_package,
        "version_code": expected_version_code,
        "version_name": expected_version_name,
    }
    for source_name, values in (("APK manifest", manifest), ("Gradle metadata", gradle)):
        for key, value in expected.items():
            if values.get(key) != value:
                raise ApkError(f"{source_name} {key}={values.get(key)!r}, expected {value!r}")
    if manifest["version_code_major"] not in (0, None):
        raise ApkError("Unexpected non-zero versionCodeMajor")
    for key, value in {
        "manager_version_code": str(expected_version_code),
        "manager_version_name": expected_version_name,
        "manager_package": expected_package,
        "uapi_version": str(expected_uapi),
    }.items():
        if build_info.get(key) != value:
            raise ApkError(f"Build metadata {key}={build_info.get(key)!r}, expected {value!r}")

    actual_signer = None
    if expected_signer_sha256:
        actual_signer = certificate_sha256(apk_path)
        expected_signer = expected_signer_sha256.replace(":", "").lower()
        if actual_signer != expected_signer:
            raise ApkError("Manager APK signer does not match the certificate embedded in the LKM")

    return {
        "apk": apk_path.name,
        "apk_sha256": hashlib.sha256(apk_path.read_bytes()).hexdigest(),
        "package": manifest["package"],
        "version_code": manifest["version_code"],
        "version_name": manifest["version_name"],
        "uapi_version": native_uapi,
        "uapi_verified_in_native_library": True,
        "signer_sha256": actual_signer or expected_signer_sha256,
        "gradle_metadata": gradle,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path)
    parser.add_argument("--gradle-metadata", type=Path, required=True)
    parser.add_argument("--build-info", type=Path, required=True)
    parser.add_argument("--expected-package", default="me.weishu.kernelsu.pr")
    parser.add_argument("--expected-version-code", type=int, default=32888)
    parser.add_argument("--expected-version-name", default="v3.3.1-32888-zfovik")
    parser.add_argument("--expected-uapi", type=int, default=8)
    parser.add_argument("--expected-signer-sha256")
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    try:
        result = verify(
            args.apk,
            args.gradle_metadata,
            args.build_info,
            args.expected_package,
            args.expected_version_code,
            args.expected_version_name,
            args.expected_uapi,
            args.expected_signer_sha256,
        )
    except (OSError, ValueError, struct.error) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    report = json.dumps(result, indent=2, ensure_ascii=True) + "\n"
    if args.json_output:
        args.json_output.write_text(report, encoding="utf-8")
    print(report, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
