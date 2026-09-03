"""Tests for the CSV log writer.

The file is the product. Everything else in this application can be run
again; a survey cannot, so these tests are mostly about the two ways a log
quietly lies to whoever reads it afterwards. The first is a column that
means one thing in the header and another in the rows, which is why the
header is checked against a literal list rather than against whatever
``columns_for`` happens to return. The second is a value that reads as a
measurement when nothing was measured, which is why an empty cell and a
zero are compared against each other in the same row rather than separately.

Rows are read back with the ``csv`` module rather than by splitting on
commas, so that a value the writer had to quote is still counted as one
cell here.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

import pytest

from greis.catalog import BY_CODE, CATALOG, HOST_TIME_COLUMN
from greis.epoch import JavadEpoch
from recording.csv_writer import CsvLogWriter, columns_for, default_log_path

PG = BY_CODE["PG"]
VG = BY_CODE["VG"]
ST = BY_CODE["ST"]
RD = BY_CODE["RD"]
NP = BY_CODE["NP"]

RECEIVED_AT = datetime(2026, 8, 5, 7, 51, 38, tzinfo=timezone.utc)


def make_epoch(**overrides: object) -> JavadEpoch:
    """An epoch with only the fields a test names. Everything else stays
    ``None``, which is what a receiver that was not asked for that message
    leaves behind."""
    fields: dict[str, object] = {"receiver_id": "javad-test", "received_at": RECEIVED_AT}
    fields.update(overrides)
    return JavadEpoch(**fields)  # type: ignore[arg-type]


def read_rows(path: Path) -> list[list[str]]:
    return list(csv.reader(path.read_text(encoding="utf-8").splitlines()))


def read_cells(path: Path) -> dict[str, str]:
    """The single data row of a one-row file, keyed by its column name."""
    header, row = read_rows(path)
    return dict(zip(header, row))


# --- the header ----------------------------------------------------------


def test_header_of_a_full_selection_is_host_time_then_catalog_order(tmp_path: Path) -> None:
    path = tmp_path / "full.csv"
    with CsvLogWriter(path, CATALOG):
        pass

    expected = ["host_time_utc"] + [column.name for message in CATALOG for column in message.columns]
    assert read_rows(path)[0] == expected


def test_header_of_a_position_only_selection_has_nothing_else_in_it(tmp_path: Path) -> None:
    # Spelled out rather than derived from the catalog: this is the header a
    # person opens the file and reads, so a change to it should have to be
    # made here too.
    path = tmp_path / "position.csv"
    with CsvLogWriter(path, [PG]):
        pass

    assert read_rows(path)[0] == [
        "host_time_utc",
        "lat_deg",
        "lon_deg",
        "alt_m",
        "pos_rms_m",
        "sol_type",
        "sol_type_label",
    ]


def test_header_is_written_even_when_no_epoch_ever_arrives(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    with CsvLogWriter(path, [PG]) as writer:
        assert writer.row_count == 0

    assert len(read_rows(path)) == 1


# --- what a cell says ----------------------------------------------------


def test_a_missing_value_is_an_empty_cell_and_a_zero_is_not(tmp_path: Path) -> None:
    path = tmp_path / "zero.csv"
    with CsvLogWriter(path, [PG, VG]) as writer:
        writer.write(
            make_epoch(latitude_deg=0.0, longitude_deg=0.0, altitude_m=0.0, sol_type=1)
        )

    cells = read_cells(path)
    assert cells["alt_m"] == "0.0000"
    assert cells["lat_deg"] == "0.000000000"
    assert cells["pos_rms_m"] == ""
    assert cells["vel_north_mps"] == ""


def test_decimals_are_honoured_exactly(tmp_path: Path) -> None:
    path = tmp_path / "decimals.csv"
    with CsvLogWriter(path, [PG]) as writer:
        writer.write(
            make_epoch(
                latitude_deg=32.0853123456789,
                longitude_deg=34.7817654321,
                altitude_m=12.34567,
                pos_rms_m=0.0125,
                sol_type=4,
            )
        )

    cells = read_cells(path)
    assert cells["lat_deg"] == "32.085312346"  # nine places, rounded not truncated
    assert cells["lon_deg"] == "34.781765432"
    assert cells["alt_m"] == "12.3457"
    assert cells["pos_rms_m"] == "0.0125"
    # No decimals declared for these two, so they are written as they are
    # rather than dressed up as floats.
    assert cells["sol_type"] == "4"
    assert cells["sol_type_label"] == "RTK Fixed"


def test_host_time_is_the_first_cell_of_every_row(tmp_path: Path) -> None:
    path = tmp_path / "host_time.csv"
    epoch = make_epoch(latitude_deg=32.0, longitude_deg=34.0)
    with CsvLogWriter(path, [PG]) as writer:
        writer.write(epoch)

    header, row = read_rows(path)
    assert header[0] == HOST_TIME_COLUMN.name
    assert row[0] == epoch.received_at.isoformat()


def test_a_derived_column_is_written_when_its_inputs_arrived(tmp_path: Path) -> None:
    path = tmp_path / "derived.csv"
    with CsvLogWriter(path, [PG, VG]) as writer:
        writer.write(
            make_epoch(
                latitude_deg=32.0,
                longitude_deg=34.0,
                vel_north_mps=3.0,
                vel_east_mps=4.0,
                vel_up_mps=0.0,
            )
        )

    cells = read_cells(path)
    assert cells["vel_ground_mps"] == "5.0000"
    assert cells["vel_3d_mps"] == "5.0000"


# --- counting and lifecycle ----------------------------------------------


def test_row_count_counts_data_rows_only(tmp_path: Path) -> None:
    path = tmp_path / "count.csv"
    writer = CsvLogWriter(path, [PG])
    assert writer.row_count == 0

    writer.open()
    assert writer.row_count == 0
    for index in range(3):
        writer.write(make_epoch(latitude_deg=32.0 + index, longitude_deg=34.0))
    assert writer.row_count == 3
    writer.close()

    assert len(read_rows(path)) == 4  # the header is not one of them


def test_write_before_open_raises(tmp_path: Path) -> None:
    writer = CsvLogWriter(tmp_path / "unopened.csv", [PG])
    with pytest.raises(RuntimeError):
        writer.write(make_epoch(latitude_deg=32.0, longitude_deg=34.0))


def test_close_twice_is_harmless(tmp_path: Path) -> None:
    path = tmp_path / "closed_twice.csv"
    writer = CsvLogWriter(path, [PG])
    writer.open()
    writer.write(make_epoch(latitude_deg=32.0, longitude_deg=34.0))
    writer.close()
    writer.close()

    assert not writer.is_open
    assert len(read_rows(path)) == 2


def test_close_without_open_is_harmless(tmp_path: Path) -> None:
    CsvLogWriter(tmp_path / "never_opened.csv", [PG]).close()


def test_the_context_manager_opens_and_closes(tmp_path: Path) -> None:
    writer = CsvLogWriter(tmp_path / "context.csv", [PG])
    assert not writer.is_open
    with writer as entered:
        assert entered is writer
        assert writer.is_open
    assert not writer.is_open


def test_path_is_the_one_it_was_given(tmp_path: Path) -> None:
    path = tmp_path / "named.csv"
    assert CsvLogWriter(path, [PG]).path == path


def test_every_row_is_on_disk_before_the_writer_is_closed(tmp_path: Path) -> None:
    """A session that is killed, or a laptop whose battery goes, must leave
    the rows it already logged behind, so each row is flushed as it is
    written rather than at close. The file is read here while the writer is
    still open, which is the only way to tell the difference."""
    path = tmp_path / "unclosed.csv"
    writer = CsvLogWriter(path, [PG])
    writer.open()
    try:
        for index in range(5):
            writer.write(make_epoch(latitude_deg=32.0 + index, longitude_deg=34.0))

        rows = read_rows(path)
        assert len(rows) == 6
        assert rows[-1][1] == "36.000000000"
    finally:
        # Only so the test does not leave a handle open on Windows; the
        # assertions above have already been made against the unclosed file.
        writer.close()


# --- columns_for ---------------------------------------------------------


def test_columns_for_uses_catalog_order_not_the_order_it_was_given() -> None:
    columns = columns_for((NP, VG, PG))
    names = [column.name for column in columns]

    assert names == (
        ["host_time_utc"]
        + [column.name for column in PG.columns]
        + [column.name for column in VG.columns]
        + [column.name for column in NP.columns]
    )


def test_columns_for_puts_host_time_first() -> None:
    assert columns_for([RD])[0] is HOST_TIME_COLUMN


def test_columns_for_an_empty_selection_still_has_a_time_axis() -> None:
    assert [column.name for column in columns_for(())] == ["host_time_utc"]


def test_columns_for_matches_the_header_the_writer_writes(tmp_path: Path) -> None:
    path = tmp_path / "agreement.csv"
    selection = [ST, PG]
    with CsvLogWriter(path, selection) as writer:
        assert [column.name for column in writer.columns] == [
            column.name for column in columns_for(selection)
        ]

    assert read_rows(path)[0] == [column.name for column in columns_for(selection)]


# --- default_log_path ----------------------------------------------------


def test_default_log_path_lands_in_the_directory_it_was_given(tmp_path: Path) -> None:
    path = default_log_path(tmp_path, "javad", RECEIVED_AT)
    assert path.parent == tmp_path
    assert path.suffix == ".csv"


def test_default_log_path_names_the_receiver(tmp_path: Path) -> None:
    assert "javad" in default_log_path(tmp_path, "javad", RECEIVED_AT).stem


def test_default_log_path_is_the_same_for_the_same_session(tmp_path: Path) -> None:
    first = default_log_path(tmp_path, "javad", RECEIVED_AT)
    second = default_log_path(tmp_path, "javad", RECEIVED_AT)
    assert first == second


def test_default_log_path_separates_two_sessions_on_the_same_day(tmp_path: Path) -> None:
    # Two runs an hour apart must not write to the same file, or the second
    # one silently replaces the first one's survey.
    morning = default_log_path(tmp_path, "javad", RECEIVED_AT)
    later = default_log_path(tmp_path, "javad", RECEIVED_AT.replace(hour=8))
    assert morning != later
