# Javad Logger

Desktop application for recording what a Javad GNSS receiver has to say.
It finds the receiver on its own, offers a list of GREIS messages to tick,
asks the receiver for the ones chosen at the rates chosen, and writes one
CSV row per position epoch.

One receiver at a time, over a serial port. There is no network transport,
no second receiver to compare against and no analysis — the file it
produces is the point, and whatever reads that file afterwards is somebody
else's program. What this has to get right is that the file contains what
was asked for, that the receiver is found without being told where it is,
and that the receiver is left exactly as it was.

## Requirements

Windows 10/11 and Python 3.11+, and a Javad receiver on a serial port. A
USB cable and a Bluetooth serial link both appear as ordinary COM ports, so
neither needs anything special here.

## Running from source

```bash
pip install -r requirements-dev.txt
python main.py
```

`requirements.txt` is the runtime list — PySide6 and pyserial, and nothing
else. `requirements-dev.txt` adds pytest on top of it.

## Building an executable

For a machine with no Python on it. The build is done in an environment of
its own, because PyInstaller refuses to run alongside the obsolete `pathlib`
backport that several scientific Python distributions still carry, and
because what ends up inside the executable should be the runtime
requirements rather than whatever else happened to be installed beside them.

```bash
python -m venv .venv-build
.venv-build/Scripts/python.exe -m pip install -r requirements.txt pyinstaller==6.11.1
.venv-build/Scripts/python.exe -m PyInstaller --noconfirm --clean javad-logger.spec
```

That leaves the application in `dist/Javad Logger/`, about 110 MB, with
`Javad Logger.exe` at the top of it. Copy or zip the whole folder — the
executable will not run without what is beside it.

A folder rather than a single file on purpose. A one-file build unpacks the
whole of Qt into a temporary directory on every start, several seconds
before the window appears, and deletes it again on exit; a folder starts
immediately, and the CSVs and the application log land beside the executable
where anybody looking for them would look. `javad-logger.spec` says what to
change to build one file anyway.

Rebuilding while a copy is still running fails on the locked executable.
Either close it first, or build somewhere else with
`--distpath dist-2` — `dist-*` is already ignored by git.

## The window

Decisions on the left — which receiver, what to log, where it goes — and
what is arriving on the right. Nothing moves between the two, so the eye
learns where to look once.

The window follows the machine between light and dark and changes with it
while it is running, because a survey tool is read outdoors in daylight and
in a vehicle at night and the operator has already told their computer
which of those they are in.

There are exactly two moving things, and both report something. The green
dot beside **Latest epoch** pulses as each epoch arrives, which answers "is
anything coming in *right now*" faster than watching a number for a few
seconds does — a receiver that has gone quiet leaves it dark. The rows
counter rolls to its new value rather than snapping, so how fast it travels
is how fast rows are being written.

Both are dropped if Windows has been asked for less animation ("Show
animations in Windows", under Settings › Accessibility › Visual effects):
the dot then stays lit while epochs arrive and the counter simply changes.
Set `JAVAD_LOGGER_REDUCED_MOTION=1` to get that behaviour without changing
the system setting.

## Finding the receiver

Adding a receiver by hand means knowing two things the machine can work out
for itself: which COM port it is on and what baud rate it is talking at.
Getting either wrong produces a connection that opens and then says
nothing, which looks identical to a dead cable.

So **Detect** sweeps instead. It opens each serial port in turn at each
baud rate in turn, listens for a short window, and looks for a GREIS
message whose checksum verifies. A verified checksum is proof rather than a
guess: it is a rotate-and-XOR over the whole message, and for it to come
out right the header, the body length and every byte between them have to
be right too. Random noise at the wrong baud rate does not produce one, and
a message that produces one will be decoded by the same checksum code the
parser uses, so a hit here means the parser will agree.

Note what is *not* required — a position. A receiver indoors, on a bench,
or still searching has nothing to report yet, and requiring a fix would
make the receiver sitting on the desk the one receiver detection cannot
find, while telling the operator to check a cable that was fine. Whether a
position has arrived is a separate question and is reported separately.

That leaves the receiver that says nothing at all, and it is a case worth
handling because this application creates it: a session ends by sending
`dm`, which switches every message off. A receiver left that way is
perfectly healthy and completely silent, and a listen-only sweep walks
straight past it. So a port that stays quiet through the listening window
gets asked a question instead — `print,/par/rcv/model:on`, to which a Javad
replies with a line containing `/par/rcv/model=` and its model name. A
receiver answers; an empty adapter, a modem or a printer does not. The
model name is worth having from a talking receiver too, and is shown beside
each result, because two identical COM ports are otherwise told apart only
by which one you unplugged.

## What you can log

Five messages. Each one contributes its own columns to the file, and
nothing else in the application needs to change when a sixth is added.

| Message | What it puts in the file |
|---|---|
| **Position** `[PG]` | Latitude, longitude and altitude, the receiver's own estimate of its position error, and the solution type — as the GREIS code and in words, so `4` and `RTK Fixed` are both there. |
| **Velocity** `[VG]` | North, east and up velocity components with the receiver's error estimate, plus the ground speed and 3D speed derived from them. |
| **Time of day** `[ST]` | The receiver's clock, as milliseconds since midnight on its own time base, written as `HH:MM:SS.mmm`. |
| **Date** `[RD]` | The receiver's date and which time base it is on. Combined with the time of day into one full UTC timestamp, with the leap-second offset applied when the base is GPS rather than UTC — so that timestamp column stays empty unless Time of day is selected as well. |
| **Satellites** `[NP]` | How many satellites of each constellation — GPS, GLONASS, Galileo, BeiDou — went into the solution, and the total. |

Position cannot be switched off. It is the position message that closes an
epoch: it is the self-contained solution, and everything else carries its
last value forward until one arrives. Without it there is no moment at
which a row becomes complete, so there would be no rows to put the velocity
or the time into. Its checkbox is shown ticked and disabled rather than
hidden, so the reason is visible instead of mysterious.

## Rates

Every ticked message has a rate beside it, and the number is a **period in
seconds** rather than a frequency, because a period is what GREIS's `em`
command takes: `em,,/msg/jps/PG:{0.01,0,0,0}` asks for a position message
every 10 ms. The hertz equivalent is shown beside the sub-second choices,
since `0.01` is easier to recognise as 100 Hz than as a hundredth of a
second.

The messages do not have to share a rate, and usually should not. Position
ten times a second with the date once every ten seconds is a sensible thing
to ask for — the date changes once a day, and asking for it at 10 Hz spends
the serial link on a value that is already known.

A slower message does not leave holes in the file. Its last value is
carried forward onto every row until the next one arrives, so a ticked
column is a filled column on every row from the first time the receiver
reports it. The rows before that first report are genuinely empty in those
columns, and that is the one gap that is real: nothing had been said yet.

The list stops at 10 ms because that is the fastest javad-udp-target drives
a Delta over a serial link, and below it the messages start to outrun the
port rather than the receiver. Several messages at 10 ms each is a
throughput question, not a receiver question, and the answer depends on the
baud rate the link came up at.

## What it sends the receiver

The complete list, in the order it goes out. Starting a session:

1. `dm` — stop every message on this port, so what follows is what the file
   holds, rather than whatever the last person left switched on.
2. One `em` per ticked message, at its period:
   `em,,/msg/jps/PG:{1,0,0,0}`, `em,,/msg/jps/NP:{10,0,0,0}`, and so on.
   The three zeros are `em`'s remaining arguments — count, delay and
   reserved — left at the defaults that mean "forever, starting now". They
   are written out rather than omitted because that is the form verified
   against real hardware, and a hex dump of a session should be comparable
   against a known-good one.

Ending a session:

3. `dm` again, so the receiver is not left streaming into a port nobody is
   reading.

Detection adds one more line, `print,/par/rcv/model:on`, asked of a port
that has gone quiet. That is the whole vocabulary: three commands.

What it deliberately does not touch is longer than what it does. Not the
baud rate — it adapts to whatever the receiver is using instead of setting
it. Not PPP, and not the correction stream. Not Bluetooth or Wi-Fi. Not
base or rover mode. Those are decisions about how the receiver *works*, and
this application only decides what it *says*. A receiver configured for
survey work is still configured for it when the session ends; the only
lasting change is which messages are enabled on the port it was plugged
into, which is exactly what asking for a log means.

## The file it writes

One row per position epoch, and one file per session. The header is built
from the selection, so the file describes itself: a column per field of
each ticked message, in the catalogue's order — where you are, how fast,
when, and with what.

`host_time_utc` comes first and is always present, whatever is selected. It
is the host's own clock, and it is the one timestamp that exists before the
receiver's date and time have arrived, which is what stops a file from ever
being without a time axis. The receiver's own timestamp sits further along
in the row and answers a different question — when the receiver says the
epoch happened, rather than when this machine saw it.

An empty cell means "not reported", never zero. A missing velocity
component is not a stationary receiver and a missing satellite count is not
an empty sky, so the two are kept apart on the page as well as in memory.
Numbers are written as the receiver sent them: nine decimals of latitude,
which is about a tenth of a millimetre — past anything a receiver can mean
and short of where the underlying double starts printing its own noise.

## Where the code came from

Very little of the hard part is new here, and that is the intention.

The GREIS decoding — the checksum, the message framing, the struct layouts,
and the resynchronisation that lets the parser recover a byte at a time
from a corrupted stream — and the port sweep that identifies a receiver by
its framing are adapted from GNSS-TrackLog.

The receiver commands come from javad-udp-target: the exact form of the
`em` argument, the periods a Delta will actually sustain over a serial
link, and the carry-forward state model that makes the position message
the thing that closes an epoch.

Both were verified against real Javad hardware, and what moved across moved
with as few edits as possible. Where a file here reads like ported code
rather than like code written for this application, that is deliberate —
the tidier version would be the version nobody has run against a receiver.
What did change is where the decoded values end up: GNSS-TrackLog reduces
velocity to speed-and-course and satellites to a single total so that
receivers of different makes can be compared, while a log file is read
later by somebody who wants the numbers the receiver actually sent, so
every field is kept as GREIS reported it and the derived ones are offered
alongside rather than instead.

## Tests

```bash
pytest
```

They run against synthetic byte streams built to the GREIS layouts, so
nothing has to be plugged in and no port is opened. `pytest.ini` puts the
repository root on `sys.path`, which is what lets the tests import `greis`
and `device` without anything being installed first.

## License

Proprietary — internal project.
