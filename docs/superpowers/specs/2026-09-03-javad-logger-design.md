# javad-logger — design

2026-09-03

## What it is

A desktop application that finds a Javad GNSS receiver on a serial port by
itself, lets the operator choose which of the receiver's messages to record
and how often, asks the receiver for exactly those, and writes one CSV row
per position epoch.

One receiver at a time. Serial only. Readable values only — no raw `.jps`
capture and no ephemeris or almanac decoding.

## Why it is a separate application

Two existing projects already talk to Javad hardware, and each has half of
what this needs.

[GNSS-TrackLog](https://github.com/louzinio/GNSS-TrackLog) has a GREIS
parser verified against real receivers, and a port sweep that identifies a
receiver by the framing of its output rather than by asking it anything.
It has no way to send a receiver a command: it only ever listens.

[javad-udp-target](https://github.com/louzinio/javad-udp-target) has the
missing half — `dm` to silence a receiver and `em,,/msg/jps/<CODE>:{...}`
to start one message at one rate — but it is a headless single-purpose tool
whose configuration is compiled into the source.

Neither is the right home for this. GNSS-TrackLog is an instrument for
comparing several receivers against a reference and against each other,
with an oscilloscope attached for the 1PPS half; adding a general-purpose
logger to it would put a second, unrelated mode inside an application that
already carries a lot. javad-udp-target is a fixed pipeline, not something
with an operator in front of it.

So: a new repository, with the verified parts of both copied into it.

## Reuse

Copied unchanged from GNSS-TrackLog, with their tests:

- `greis/checksum.py` — the rotate-left-2-then-XOR accumulator.
- `greis/messages.py` — the message structs and their parsers.

Adapted, because the destination type changed:

- `greis/parser.py` — the framing and resynchronisation logic, unchanged in
  substance. What changed is what it produces. GNSS-TrackLog's parser fills
  a `GnssFix`, a type shaped for comparing receivers of any make, so it
  reduces velocity to speed-and-course and satellite counts to a single
  total. A log file is read afterwards by somebody who wants the numbers
  the receiver actually sent, so `JavadEpoch` keeps every field.
- `device/serial_port.py` — the transport, with `write_line` added.
- `device/discovery.py` — the port sweep, narrowed to GREIS only and
  extended with a model query (see below).

Adapted from javad-udp-target:

- `greis/commands.py` — the two commands, generalised so the message set
  and the rates come from the operator rather than from the source.

## What it sends the receiver

Only this, and it is worth being exact because sending a survey receiver
the wrong thing costs somebody a day:

- `dm` — stop every message on this port. Sent at the start of a session,
  so the log holds what was asked for and not what the last person left
  running, and again at the end.
- `em,,/msg/jps/<CODE>:{<period>,0,0,0}` — one per selected message.
- `print,/par/rcv/model:on` — during detection only, and only to fill in
  the receiver's name. It reads a parameter and changes nothing.

Nothing else. Not the baud rate, not PPP or the correction stream, not
Bluetooth or Wi-Fi, not base or rover mode. A receiver configured for
survey work is still configured for it when the session ends; the only
lasting change is which messages are enabled, which is what asking for a
log means.

## Detection

The sweep opens each serial port in turn and, at each baud rate in a list
ordered by how likely a Javad is to be using it, listens for about 1.6
seconds and feeds everything to the GREIS parser. A verified GREIS checksum
does not happen by accident, so a single accepted message is proof.

A port that will not open is abandoned immediately rather than retried at
every baud rate: it is busy or gone, and that is true at all of them.

Where this differs from GNSS-TrackLog's sweep: a port that delivers no
bytes at all is not written off. GNSS-TrackLog can assume a silent line is
a dead line, because it never configures anything. This application does —
so the most likely reason a Javad is silent is that this application itself
sent it `dm` at the end of the last session. A silent port therefore gets
one `print,/par/rcv/model:on` and half a second to answer. A receiver that
names itself is found; a genuinely dead line still says nothing and costs
the extra half second per baud rate.

## The message catalog

| Message | What it contributes | Columns |
|---|---|---|
| **[PG]** Position | Where the receiver thinks it is, its own error estimate, and the solution type | `lat_deg`, `lon_deg`, `alt_m`, `pos_rms_m`, `sol_type`, `sol_type_label` |
| **[VG]** Velocity | The three velocity components and their error estimate, plus the two derived speeds | `vel_north_mps`, `vel_east_mps`, `vel_up_mps`, `vel_rms_mps`, `vel_ground_mps`, `vel_3d_mps` |
| **[ST]** Time of day | The receiver's clock, as milliseconds since midnight | `rx_time_of_day` |
| **[RD]** Date | The date and which time base it is on | `rx_date`, `rx_time_base`, `rx_datetime_utc` |
| **[NP]** Satellites | How many satellites of each constellation are in the solution | `sv_gps`, `sv_glonass`, `sv_galileo`, `sv_beidou`, `sv_total` |

`host_time_utc` is always the first column, whatever is selected. It is the
one timestamp that exists for every row even before the receiver's clock is
known, so a file is never without a time axis.

**Position cannot be switched off.** GREIS messages carry no shared epoch
identifier the way NMEA sentences share a UTC timestamp, so [PG] — the
self-contained position solution — is what closes an epoch and turns the
current state into a row. With it disabled there would be no moment at
which a row becomes complete. The GUI shows it ticked and disabled rather
than hiding it, so the reason is visible.

`rx_datetime_utc` needs both [ST] and [RD]: without the date there is no
timestamp to assemble, and without [RD]'s base field there is no way to
know whether to subtract the 18 leap seconds. Guessing would put the
column 18 seconds out with nothing in the file to say so, so it stays
empty instead.

## Rates

The number the operator picks is a **period in seconds**, because that is
what GREIS's `em` takes: `{0.01,0,0,0}` asks for a message every 10 ms. The
GUI shows the equivalent in hertz beside it, since a period of 0.01 is
easier to recognise as 100 Hz.

Messages at different rates share a row. A slower message carries its last
known value forward onto every row until the next one arrives, rather than
leaving nine rows in ten empty — which is what javad-udp-target's
continuously-mutated state object does, and what somebody reading the file
expects when they set the satellite count to 1 s and the position to 100
ms.

## Architecture

```
main.py                  logging setup, QApplication, the window
gui/theme.py             colours, fonts, the stylesheet
gui/main_window.py       the one window
device/discovery.py      the port sweep and its worker thread
device/serial_port.py    pyserial transport, reads and command writes
device/session.py        the worker thread that runs one logging session
recording/csv_writer.py  the header, the formatting, the flush
greis/catalog.py         what can be logged and what columns it produces
greis/commands.py        the GREIS commands, and only those
greis/parser.py          bytes to epochs
greis/epoch.py           JavadEpoch
greis/epoch_builder.py   the carried-forward state
greis/messages.py        struct decoding          (copied, verified)
greis/checksum.py        the checksum             (copied, verified)
```

The dependency direction is one-way: `gui` knows about `device` and
`greis`; `device` knows about `greis` and `recording`; `greis` knows about
nothing but itself. `greis/` and `recording/` are Qt-free and testable
without a `QApplication`; the two `QThread`s are the only Qt in the
non-GUI layers, and they compose the plain classes rather than being them.

## Data flow

```
SerialPort.read()  ->  GreisParser.feed()  ->  JavadEpoch
                                                 |
                                    +------------+------------+
                                    |                         |
                            CsvLogWriter.write()      epoch_logged signal
                            (every epoch)             (throttled to ~2 Hz)
                                                              |
                                                       the live panel
```

The throttle is a display concern only. Every epoch reaches the file; the
GUI cannot use 100 rows a second and the queued signals would become the
bottleneck if it tried.

## Error handling

- A port that will not open at the start: the session reports why and
  stops, without having created a file.
- A port that fails mid-session: the session reports it, closes the file
  properly and stops. Every row up to that point is on disk, because the
  writer flushes after each one.
- The silence watchdog: if nothing has been written five seconds after the
  commands went out, one line says so and names the enabled messages. The
  likely causes are the wrong baud rate, no antenna, or a receiver that has
  not acquired yet — and without this the operator watches an empty table
  and learns nothing.
- A row whose value will not format: the cell falls back to `str()` rather
  than raising. A session that dies mid-run loses data; an odd-looking cell
  does not.
- Closing the window during a session stops it and waits before accepting
  the close, so the file is closed properly.

## Testing

`pytest`, no hardware. The copied checksum and message tests come across
unchanged. New tests cover the parser's framing and resynchronisation, the
builder's time assembly and carry-forward, the exact text of every command,
the CSV header and formatting for a given selection, and a whole session
driven over a fake serial port with scripted bytes.

The distinction the CSV tests exist to protect: an empty cell and a zero
are different facts, and the file has to keep them apart.

## Out of scope

Deliberately not built, and each for a reason:

- **Raw `.jps` capture.** Asked for and declined: the requirement is
  readable values.
- **More than one receiver at once.** The measurement application already
  does that; this one is a logger, and a single-receiver window is a much
  simpler window.
- **TCP and Bluetooth transports.** Bluetooth already appears as a COM port
  on Windows and needs nothing. TCP is at an address somebody has to know,
  which is a different dialogue for a receiver nobody has here.
- **Ephemeris, ionosphere and almanac messages ([GE], [IO], [UO], [GA]).**
  They are numbers for a processing pipeline, not values to read in a
  table. The catalog is one entry per message, so adding them later is an
  entry and a decoder.
- **Changing anything on the receiver but its message set.**
