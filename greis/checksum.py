"""GREIS message checksum: an 8-bit rotate-left-2-then-XOR accumulator,
verified against real Javad hardware in the companion javad-udp-target
project's ``src/javad.py::JAVAD.check_CS``."""

from __future__ import annotations


def _rotate_left_2(value: int) -> int:
    return ((value << 2) | (value >> 6)) & 0xFF


def compute_checksum(payload: bytes) -> int:
    checksum = 0
    for byte in payload:
        checksum = _rotate_left_2(checksum) ^ byte
    return _rotate_left_2(checksum)


def verify_checksum(payload: bytes, received_checksum: int) -> bool:
    return compute_checksum(payload) == received_checksum
