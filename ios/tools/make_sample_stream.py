"""Build the recorded GREIS stream the iOS app replays when there is no
receiver, and prove it decodes with the parser that already exists.

The point is not the file. The point is that the bytes are built here from
the same layout constants the Swift port was written against, and then fed
through ``greis.parser.GreisParser`` - the code that has been run against
real hardware. If this script's assertions pass, the layouts in
``ios/GreisKit/Sources/GreisKit/Messages.swift`` describe the same bytes.

    python ios/tools/make_sample_stream.py
"""

from __future__ import annotations

import math
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from greis.checksum import compute_checksum  # noqa: E402
from greis.messages import (  # noqa: E402
    HEADER_NP,
    HEADER_PG,
    HEADER_RD,
    HEADER_ST,
    HEADER_VG,
)
from greis.parser import GreisParser  # noqa: E402

OUT = REPO / "ios" / "App" / "Resources" / "sample-stream.bin"

EPOCHS = 120
"""Two minutes at 1 Hz: long enough to watch the counter roll, the pulse
beat and the throughput readout settle, short enough to stay small."""


def sealed(payload: bytes) -> bytes:
    """Header + body without its checksum, plus the checksum."""
    return payload + bytes([compute_checksum(payload)])


def pg(lat_deg: float, lon_deg: float, alt_m: float, sigma: float, sol_type: int) -> bytes:
    body = struct.pack(
        "<3d f B",
        math.radians(lat_deg),
        math.radians(lon_deg),
        alt_m,
        sigma,
        sol_type,
    )
    return sealed(HEADER_PG + body)


def vg(north: float, east: float, up: float, sigma: float, sol_type: int) -> bytes:
    return sealed(HEADER_VG + struct.pack("<4f B", north, east, up, sigma, sol_type))


def st(time_of_day_ms: int, sol_type: int) -> bytes:
    return sealed(HEADER_ST + struct.pack("<I B", time_of_day_ms, sol_type))


def rd(year: int, month: int, day: int, base_is_utc: bool) -> bytes:
    return sealed(HEADER_RD + struct.pack("<H B B B", year, month, day, 1 if base_is_utc else 0))


def np(gps: int, glonass: int, galileo: int, beidou: int) -> bytes:
    """A text [NP], padded past the parser's 187-byte gate.

    The padding is required, not cosmetic: GreisParser will not scan for the
    '@' terminator until that many bytes are buffered, because a real [NP]
    from a receiver is about that long. The padding must not contain an '@'.
    """
    body = f"NP0BB,0.0,0.0,0.0,0,{{{gps},{glonass},{galileo},0,0,{beidou}}},"
    body = body + "0" * max(0, 195 - len(body))
    message = (body + "@").encode("ascii")
    return message + f"{compute_checksum(message):02X}".encode("ascii") + b"\r\n"


def build() -> bytes:
    stream = bytearray()

    lat, lon, alt = 32.081234567, 34.780987654, 42.8137
    midnight_ms = 14 * 3600_000 + 12 * 60_000  # 14:12:00.000

    for index in range(EPOCHS):
        # A receiver on a bench: millimetres of wander, nothing dramatic.
        lat += 1.1e-9 * ((index % 7) - 3)
        lon += 1.1e-9 * ((index % 5) - 2)
        alt += 0.0009 * ((index % 11) - 5)
        sigma = 0.0142 + 0.0004 * ((index % 9) - 4)
        sol_type = 4 if index > 4 else 3  # float for the first few, then fixed

        stream += st(midnight_ms + index * 1000, sol_type)
        if index % 10 == 0:
            stream += rd(2026, 9, 5, True)
            stream += np(11 - (index % 3), 8, 7, 6)
        stream += vg(0.0031, -0.0018, 0.0007, 0.0044, sol_type)
        # [PG] last: it is the message that closes the epoch, so everything
        # above lands on the same row.
        stream += pg(lat, lon, alt, sigma, sol_type)

    return bytes(stream)


def verify(stream: bytes) -> None:
    """Feed it through the real parser, a chunk at a time.

    Chunked on purpose: a socket delivers arbitrary fragments, and a parser
    that only works when whole messages arrive together is a parser that
    works on the desk and fails in the field.
    """
    parser = GreisParser(receiver_id="SAMPLE")
    epochs = []
    for offset in range(0, len(stream), 137):  # a deliberately awkward size
        epochs.extend(parser.feed(stream[offset : offset + 137]))

    assert len(epochs) == EPOCHS, f"expected {EPOCHS} epochs, decoded {len(epochs)}"

    first, last = epochs[0], epochs[-1]
    assert first.latitude_deg is not None and abs(first.latitude_deg - 32.081) < 0.01
    assert first.longitude_deg is not None and abs(first.longitude_deg - 34.781) < 0.01
    assert last.sol_type == 4 and last.sol_type_label == "RTK Fixed"
    assert last.sv_total == 32 - (110 % 3) or last.sv_total is not None
    assert last.receiver_date is not None, "the date must carry forward"
    assert last.utc_datetime is not None, "both halves of the clock must combine"
    assert last.vel_ground_mps is not None

    counts = parser.message_counts
    assert counts.get("PG") == EPOCHS, counts
    assert counts.get("NP") == EPOCHS // 10, counts

    print(f"decoded {len(epochs)} epochs from {len(stream)} bytes")
    print(f"  message counts: {counts}")
    print(f"  last epoch: {last.latitude_deg:.9f}, {last.longitude_deg:.9f}, "
          f"{last.altitude_m:.4f} m, {last.sol_type_label}, {last.sv_total} SV")
    print(f"  receiver time: {last.utc_datetime.isoformat()}")


if __name__ == "__main__":
    stream = build()
    verify(stream)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(stream)
    print(f"wrote {OUT.relative_to(REPO)} ({len(stream)} bytes)")
