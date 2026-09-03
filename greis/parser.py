"""Turns a Javad receiver's byte stream into :class:`JavadEpoch` records.

GREIS frames are binary: a 5-byte message-id header, a struct-packed body,
and a checksum. [NP] is the exception - it is text, terminated by
``@<checksum><CR><LF>`` - so it is framed by its terminator rather than by
a fixed length.

Synchronisation is by header match. Each candidate 5-byte header is
compared against the messages this parser understands; anything else costs
one dropped byte and a retry, which is how the stream recovers from a
corrupted byte or from a message type that was left enabled on the
receiver from some earlier session.

The framing here is carried over unchanged from the GNSS-TrackLog parser
that was verified against real Javad hardware. What changed is where the
decoded values go: a :class:`JavadEpoch` keeps every field GREIS sent,
rather than reducing velocity to speed-and-course for cross-vendor
comparison.
"""

from __future__ import annotations

import logging

from greis.checksum import verify_checksum
from greis.epoch import JavadEpoch
from greis.epoch_builder import GreisEpochBuilder
from greis.messages import (
    HEADER_LEN,
    HEADER_NP,
    HEADER_PG,
    HEADER_RD,
    HEADER_ST,
    HEADER_VG,
    MSG_LEN_NP_MIN_BUFFERED,
    MSG_LEN_PG,
    MSG_LEN_RD,
    MSG_LEN_ST,
    MSG_LEN_VG,
    parse_np_satellite_counts_from_message,
    parse_pg,
    parse_rd,
    parse_st,
    parse_vg,
)

_logger = logging.getLogger(__name__)

MAX_BUFFER_BYTES = 8192
"""A ceiling on unparsed bytes. Reached only when the stream is not GREIS
at all - a correct one never holds more than one message plus a fragment -
so the buffer is dropped rather than grown without limit."""


class GreisParser:
    """Stateful GREIS parser for one receiver's byte stream.

    Not thread-safe by design: one parser belongs to one reader thread, the
    same way one connection does.
    """

    def __init__(self, receiver_id: str, *, log_raw: bool = False) -> None:
        self._receiver_id = receiver_id
        self._log_raw = log_raw
        self._buffer = bytearray()
        self._builder = GreisEpochBuilder(receiver_id=receiver_id)
        self._message_counts: dict[str, int] = {}

    @property
    def message_counts(self) -> dict[str, int]:
        """How many of each message type have been accepted, by code -
        ``{"PG": 120, "NP": 2}``. What tells "the receiver is sending, the
        checksums are good, but you never asked for [RD]" apart from "the
        cable is dead"."""
        return dict(self._message_counts)

    def feed(self, data: bytes) -> list[JavadEpoch]:
        """Consume newly-read bytes.

        Returns the epochs completed by this call - usually 0 or 1, but a
        read that spans several [PG]s completes one per position message.
        """
        if not data:
            return []
        self._buffer.extend(data)
        epochs: list[JavadEpoch] = []

        while True:
            consumed, epoch = self._consume_one()
            if not consumed:
                break
            if epoch is not None:
                epochs.append(epoch)

        if len(self._buffer) > MAX_BUFFER_BYTES:
            _logger.warning(
                "%s: discarding %d unparsed bytes - this does not look like GREIS",
                self._receiver_id,
                len(self._buffer),
            )
            self._buffer.clear()

        return epochs

    def reset(self) -> None:
        """Forget the partial buffer and the carried-forward state, so a
        reconnect does not put values from before the disconnection into
        rows recorded after it."""
        self._buffer.clear()
        self._builder = GreisEpochBuilder(receiver_id=self._receiver_id)

    # --- framing ---------------------------------------------------------

    def _consume_one(self) -> tuple[bool, JavadEpoch | None]:
        if len(self._buffer) < HEADER_LEN:
            return False, None

        header = bytes(self._buffer[:HEADER_LEN])

        if header == HEADER_PG:
            return self._consume_fixed("PG", MSG_LEN_PG, parse_pg, self._on_pg)
        if header == HEADER_VG:
            return self._consume_fixed("VG", MSG_LEN_VG, parse_vg, self._on_vg)
        if header == HEADER_ST:
            return self._consume_fixed("ST", MSG_LEN_ST, parse_st, self._on_st)
        if header == HEADER_RD:
            return self._consume_fixed("RD", MSG_LEN_RD, parse_rd, self._on_rd)
        if header == HEADER_NP:
            return self._consume_np()

        del self._buffer[0:1]
        return True, None

    def _consume_fixed(
        self, code: str, message_len: int, parse_body, apply
    ) -> tuple[bool, JavadEpoch | None]:
        if len(self._buffer) < message_len:
            return False, None

        message = bytes(self._buffer[:message_len])
        del self._buffer[:message_len]

        if not verify_checksum(message[:-1], message[-1]):
            _logger.debug("%s: [%s] checksum mismatch, dropped", self._receiver_id, code)
            return True, None

        if self._log_raw:
            _logger.debug("%s RX: %s", self._receiver_id, message.hex())

        # The body still carries its trailing checksum byte, which the
        # struct formats in greis.messages consume as their final field.
        try:
            parsed = parse_body(message[HEADER_LEN:])
        except Exception:
            _logger.exception("%s: could not parse [%s]: %s", self._receiver_id, code, message.hex())
            return True, None

        self._count(code)
        return True, apply(parsed)

    def _consume_np(self) -> tuple[bool, JavadEpoch | None]:
        buffer = self._buffer
        if len(buffer) < MSG_LEN_NP_MIN_BUFFERED:
            return False, None

        at_index = buffer.find(b"@")
        if at_index == -1:
            return False, None
        if len(buffer) < at_index + 5:
            return False, None  # checksum digits and CRLF not here yet

        is_terminator = buffer[at_index + 3] == 0x0D and buffer[at_index + 4] == 0x0A
        if not is_terminator:
            # A stray '@' in the message text. Drop up to and including it
            # and look for the real terminator on the next call.
            del buffer[: at_index + 1]
            return True, None

        message = bytes(buffer[: at_index + 1])  # header ... '@'
        checksum_text = bytes(buffer[at_index + 1 : at_index + 3]).decode("ascii", errors="ignore")
        del buffer[: at_index + 5]

        try:
            checksum = int(checksum_text, 16)
        except ValueError:
            return True, None

        if not verify_checksum(message, checksum):
            _logger.debug("%s: [NP] checksum mismatch, dropped", self._receiver_id)
            return True, None

        if self._log_raw:
            _logger.debug("%s RX: %s", self._receiver_id, message)

        counts = parse_np_satellite_counts_from_message(message)
        self._count("NP")
        if counts is not None:
            self._on_np(counts)
        return True, None

    def _count(self, code: str) -> None:
        self._message_counts[code] = self._message_counts.get(code, 0) + 1

    # --- applying a decoded message --------------------------------------

    def _on_pg(self, message) -> JavadEpoch:
        self._builder.latitude_deg = message.latitude_deg
        self._builder.longitude_deg = message.longitude_deg
        self._builder.altitude_m = message.altitude_m
        self._builder.pos_rms_m = message.pos_sigma_m
        self._builder.sol_type = message.sol_type
        return self._builder.snapshot()

    def _on_vg(self, message) -> None:
        self._builder.vel_north_mps = message.vel_north_mps
        self._builder.vel_east_mps = message.vel_east_mps
        self._builder.vel_up_mps = message.vel_up_mps
        self._builder.vel_rms_mps = message.vel_sigma_mps
        return None

    def _on_st(self, message) -> None:
        self._builder.time_of_day_ms = message.time_of_day_ms
        return None

    def _on_rd(self, message) -> None:
        self._builder.date_year = message.year
        self._builder.date_month = message.month
        self._builder.date_day = message.day
        self._builder.base_is_utc = message.base_is_utc
        return None

    def _on_np(self, counts: dict[str, int]) -> None:
        self._builder.sv_gps = counts.get("gps")
        self._builder.sv_glonass = counts.get("glonass")
        self._builder.sv_galileo = counts.get("galileo")
        self._builder.sv_beidou = counts.get("beidou")
