# Javad Logger for iPhone

The same job as the desktop application — ask a Javad receiver for a
handful of GREIS messages and write one CSV row per position epoch — over
the one route into a receiver that iOS actually has.

## What is verified and what is not

Read this first, because it decides how much of the rest to trust.

**Verified.** The byte layouts. `ios/tools/make_sample_stream.py` builds the
replay stream from the same constants the Swift port was written against
and then feeds it through `greis/parser.py` — the parser that has been run
against real hardware. It decodes 120 epochs, with the message counts, the
carry-forward and the combined receiver timestamp all coming out right. If
that script passes, `Messages.swift` describes the same bytes the Python
does.

**Not verified.** Everything Swift. This was written on Windows with no
Xcode, no SDK and no device, so **not one line of it has been compiled.**
Expect to fix build errors on the first `xcodegen generate && build` — the
shape of the code is right, individual API signatures may not be.

**Not tested against hardware.** No receiver was available. `TCPTransport`
is written to JAVAD's documented defaults but has never opened a socket to
a real one.

The single riskiest file is `App/Glass.swift`, and it is deliberately the
only one that touches the iOS 26 Liquid Glass API. The default build path
uses `.ultraThinMaterial`, which is certain to compile; glass is behind the
`USE_LIQUID_GLASS` compilation condition, commented out in `project.yml`.

## Building it

You need a Mac. There is no way to build an iOS app from Windows, and no
amount of tooling changes that.

```bash
brew install xcodegen
cd ios
xcodegen generate
open JavadLogger.xcodeproj
```

Then set your own team and bundle id in *Signing & Capabilities*. A free
Apple ID signs the app for seven days at a time and needs the phone
plugged in; the paid Developer Program signs it for a year.

The part that needs no Mac to be useful is the package, and it is the part
worth running first:

```bash
cd ios/GreisKit
swift test
```

That exercises the checksum, the framing and resynchronisation, the
carry-forward, the leap-second handling, the CSV writer's header rules and
a whole replayed session end to end. No simulator, no receiver.

## The connection

From JAVAD's own iOS manual, for a receiver acting as its own access point:

| | |
|---|---|
| Host | `192.168.0.1` — the receiver's address in adhoc mode, and its gateway |
| Port | `8002`, set with `set,/par/net/tcp/port,8002` |
| Password | `set,/par/net/passwd,"1234"` — guards TCP and FTP together |
| SSID | `set,/par/net/wlan/ap/ssid,…` (`TRIUMPH2_008` in the manual) |
| Mode | `set,/par/net/wlan/mode,adhoc`, DHCP server on, client off |
| | then `set,reset,yes` — Wi-Fi changes do not take effect until the receiver restarts |

Ports 8010–8014 are a different job — RTN correction streams — and are not
what this app wants.

Set those over a cable with JTerm or NetView once, and the receiver keeps
them.

## Why this is not the desktop application

Four things had no iOS route and were removed rather than drawn as controls
that would never work.

**Serial.** iOS has no serial API, and will not open a Bluetooth SPP link
without MFi certification. TCP to the receiver's own Wi-Fi is the only way
in. Everything above the transport is unchanged, which is why
`GreisTransport` exists: the parser cannot tell a socket from a replayed
file.

**The baud sweep.** There is no baud rate on a socket. What survived is the
*identification*, which never depended on the transport — listen for a
verified checksum, and if the link stays quiet, ask
`print,/par/rcv/model:on`, because a receiver silenced by an earlier `dm`
is healthy and completely mute.

**Choosing a folder.** iOS gives no free filesystem. Files land in the
app's own container and are visible in Files under *On My iPhone › Javad
Logger*, which is what `UIFileSharingEnabled` and
`LSSupportsOpeningDocumentsInPlace` in `Info.plist` are for.

**Background recording.** iOS suspends a backgrounded app and closes its
sockets. Rather than fail mid-row, the app stops the session deliberately
when it leaves the foreground, sends its `dm`, closes the file and says why.
`isIdleTimerDisabled` keeps the screen on while recording.

Two things iOS adds that the desktop never had to think about: the Local
Network permission prompt on the first socket to a device on the same
Wi-Fi — asked for at the Connect tap, when the reason is on screen, because
a denial can only be undone in Settings — and the fact that the receiver's
access point has no internet, so the connection is pinned to Wi-Fi or iOS
will route it over cellular and time out against an address only reachable
locally.

## Without a receiver

Turn on **Replay a recorded stream** in the Link tab. It plays
`App/Resources/sample-stream.bin` at roughly receiver speed instead of
opening a socket, so the pulse, the rolling counter, the carry-forward and
the CSV writer all run in the Simulator with nothing plugged in. No
commands are sent — there is nothing on the other end to obey them.

Regenerate the file, and re-check it against the Python parser, with:

```bash
python ios/tools/make_sample_stream.py
```

It is also the right first move when something goes wrong in the field:
capture the bytes, replay them, and the bug reproduces without needing the
receiver, the weather or the site again.

## Layout

```
ios/
  project.yml              XcodeGen spec — no .xcodeproj is checked in
  GreisKit/                everything that is not a screen
    Sources/GreisKit/
      Checksum.swift       rotate-left-2 then XOR
      Messages.swift       headers, layouts, little-endian reads
      Parser.swift         framing and resynchronisation
      Epoch.swift          one row, every field optional
      EpochBuilder.swift   carry-forward; [PG] closes the epoch
      Catalog.swift        the five messages and their columns
      Commands.swift       dm, em, print — the whole vocabulary
      CSVWriter.swift      header built from the selection
      Transport.swift      what a session needs from underneath it
      TCPTransport.swift   NWConnection, pinned to Wi-Fi
      ReplayTransport.swift recorded bytes, played back
      Session.swift        connect, ask, parse, write
    Tests/GreisKitTests/
  App/
    Glass.swift            the only file that knows about Liquid Glass
    AppModel.swift         settings, link state, session state
    Views/                 Link, Log, Record, Files
    Resources/sample-stream.bin
  tools/make_sample_stream.py
```

## What is deliberately still missing

- No RTK corrections, no NTRIP, no base or rover configuration. The app
  decides what the receiver *says*, not how it *works*, and nothing under
  `/par/net` is written — changing the network settings is how you
  disconnect yourself from the device you are talking to.
- No app icon.
- The password is sent as `set,/par/passwd,…` on connect and stored in
  `UserDefaults`. Before this is used for anything real it belongs in the
  keychain.
