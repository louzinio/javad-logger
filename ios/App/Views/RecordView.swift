import SwiftUI
import GreisKit

/// Rows, the latest epoch, and the pulse.
///
/// Built from `Card` rather than from a grouped `List`, because a list
/// style does not let its corner radius be chosen and the rounding was the
/// visible difference from the design. The settings screens stay as forms -
/// they are settings - but this screen is a readout.
///
/// There is exactly one moving thing on it and it reports something: the
/// dot beats once per epoch, which answers "is anything arriving *right
/// now*" faster than watching a number for a few seconds does. A receiver
/// that has gone quiet leaves it still.
struct RecordView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var beat = false
    @State private var elapsed: TimeInterval = 0

    private let tick = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    var body: some View {
        @Bindable var model = model

        NavigationStack {
            ScrollView {
                VStack(spacing: 0) {
                    sessionCard

                    if let error = model.lastError {
                        CardHeader(title: "Stopped")
                        Card(radius: 22) {
                            Text(error)
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }

                    CardHeader(title: "Position", code: "PG")
                    Card(radius: 22) {
                        CardRow(label: "Latitude", first: true) { value(model.latest?.latitudeDeg.fixed(9, suffix: "°")) }
                        CardRow(label: "Longitude") { value(model.latest?.longitudeDeg.fixed(9, suffix: "°")) }
                        CardRow(label: "Altitude") { value(model.latest?.altitudeM.fixed(4, suffix: " m")) }
                        CardRow(label: "Position RMS") { value(model.latest?.posRmsM.fixed(4, suffix: " m")) }
                    }

                    CardHeader(title: "Velocity", code: "VG")
                    Card(radius: 22) {
                        CardRow(label: "North", first: true) { value(model.latest?.velNorthMps.fixed(4)) }
                        CardRow(label: "East") { value(model.latest?.velEastMps.fixed(4)) }
                        CardRow(label: "Up") { value(model.latest?.velUpMps.fixed(4)) }
                        CardRow(label: "Ground speed") { value(model.latest?.velGroundMps.fixed(4, suffix: " m/s")) }
                    }

                    CardHeader(title: "Satellites", code: "NP")
                    Card(radius: 22) {
                        CardRow(label: "GPS", first: true) { value(model.latest?.svGPS.map(String.init)) }
                        CardRow(label: "GLONASS") { value(model.latest?.svGLONASS.map(String.init)) }
                        CardRow(label: "Galileo") { value(model.latest?.svGalileo.map(String.init)) }
                        CardRow(label: "BeiDou") { value(model.latest?.svBeiDou.map(String.init)) }
                        CardRow(label: "Total") { value(model.latest?.svTotal.map(String.init)) }
                    }

                    CardHeader(title: "Socket")
                    Card(radius: 22) {
                        CardRow(label: "Throughput", first: true) {
                            value((model.bytesPerSecond / 1024).fixed(1) + " kB/s")
                        }
                        CardRow(label: "Since last byte") {
                            value(model.secondsSinceLastByte.fixed(2) + " s")
                        }
                    }
                    CardFooter(text: "A receiver that has gone quiet and a socket that has dropped look identical to the parser, so both are reported: the dot stops beating and the gap keeps climbing.")

                    CardHeader(title: "While recording")
                    Card(radius: 22) {
                        Toggle(isOn: $model.keepScreenAwake) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text("Keep the screen awake")
                                Text("iOS suspends a backgrounded app and closes its sockets, which ends the file.")
                                    .font(.footnote)
                                    .foregroundStyle(.secondary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        .tint(.accentColor)
                    }

                    CardHeader(title: "Sent to the receiver")
                    Card(radius: 22) {
                        Text(commandText)
                            .font(.system(.caption, design: .monospaced))
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    CardFooter(text: "dm again on Stop, so the receiver is not left streaming into a socket nobody is reading. Nothing else is touched: not PPP, not the corrections, not base or rover mode, and nothing under /par/net.")

                    Color.clear.frame(height: 28)
                }
            }
            .background(.ground)
            .navigationTitle("Recording")
            .onReceive(tick) { _ in
                guard let started = model.startedAt, model.isRecording else { return }
                elapsed = Date().timeIntervalSince(started)
            }
            .onChange(of: model.rowCount) { _, _ in
                guard !reduceMotion else { return }
                beat.toggle()
            }
        }
    }

    // MARK: The session

    /// The one card that floats: it is the session's own chrome, and the
    /// control that ends the session belongs in it rather than hovering
    /// over readings it has nothing to do with.
    private var sessionCard: some View {
        Card(radius: 30, glass: true) {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Label {
                        Text(model.latest?.solTypeLabel ?? (model.isRecording ? "Waiting" : "Stopped"))
                            .font(.subheadline.weight(.semibold))
                    } icon: {
                        Circle()
                            .fill(model.isRecording ? Color.green : Color.secondary)
                            .frame(width: 8, height: 8)
                            .scaleEffect(beat && model.isRecording ? 1.35 : 1.0)
                            .animation(.settle, value: beat)
                    }
                    Spacer()
                    Text(formattedElapsed)
                        .font(.system(.footnote, design: .monospaced))
                        .foregroundStyle(.secondary)
                }

                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(model.rowCount, format: .number)
                        .font(.system(size: 46, weight: .semibold, design: .rounded))
                        .monospacedDigit()
                        .contentTransition(.numericText())
                        .animation(.settle, value: model.rowCount)
                    Text("rows written").foregroundStyle(.secondary)
                }

                Text(model.link.useReplay ? "Replaying a recorded stream" : "\(model.receiverModel ?? "Receiver") · \(model.link.host):\(model.link.port)")
                    .font(.footnote)
                    .foregroundStyle(.secondary)

                Button(role: model.isRecording ? .destructive : nil) {
                    if model.isRecording { model.stopRecording() }
                    else { model.startRecording(); elapsed = 0 }
                } label: {
                    Text(model.isRecording ? "Stop and close the file" : "Start a new file")
                        .frame(maxWidth: .infinity)
                }
                // Bordered rather than prominent: a prominent button
                // fills itself with the accent and puts white type on
                // top, and white on JAVAD's lime measures 2.1:1. This
                // tints the capsule and colours the label instead.
                .buttonStyle(.bordered)
                .controlSize(.large)
                .buttonBorderShape(.capsule)

                if let url = model.fileToShare, !model.isRecording {
                    ShareLink(item: url) {
                        Label("Share \(url.lastPathComponent)", systemImage: "square.and.arrow.up")
                            .font(.footnote)
                    }
                }
            }
        }
        .padding(.top, 4)
    }

    private func value(_ text: String?) -> some View {
        Text(text ?? "—")
            .font(.system(.subheadline, design: .monospaced))
            .monospacedDigit()
            .foregroundStyle(text == nil ? .tertiary : .primary)
    }

    private var commandText: String {
        GreisCommands.startLogging(model.selection.requests).joined(separator: "\n")
    }

    private var formattedElapsed: String {
        let total = Int(elapsed)
        return String(format: "%02d:%02d:%02d", total / 3600, (total % 3600) / 60, total % 60)
    }
}
