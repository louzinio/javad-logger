import SwiftUI
import GreisKit

/// Rows, the latest epoch, and the pulse.
///
/// There is exactly one moving thing on this screen and it reports
/// something: the dot beats once per epoch, which answers "is anything
/// arriving *right now*" faster than watching a number for a few seconds
/// does. A receiver that has gone quiet leaves it dark.
struct RecordView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var beat = false
    @State private var elapsed: TimeInterval = 0

    private let tick = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    var body: some View {
        @Bindable var model = model

        NavigationStack {
            List {
                sessionSection

                if let error = model.lastError {
                    Section {
                        Text(error).font(.footnote).foregroundStyle(.secondary)
                    } header: {
                        Label("Stopped", systemImage: "exclamationmark.triangle")
                    }
                }

                positionSection
                velocitySection
                satellitesSection
                socketSection

                Section {
                    Toggle("Keep the screen awake", isOn: $model.keepScreenAwake)
                } footer: {
                    Text("iOS suspends a backgrounded app and closes its sockets, which ends the file. Leaving the app while recording stops the session deliberately, so the rows already written are complete.")
                }

                commandsSection
            }
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

    // MARK: Sections

    private var sessionSection: some View {
        Section {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Label {
                        Text(model.latest?.solTypeLabel ?? (model.isRecording ? "Waiting" : "Stopped"))
                            .font(.subheadline.weight(.semibold))
                    } icon: {
                        Circle()
                            .fill(model.isRecording ? Color.green : Color.secondary)
                            .frame(width: 8, height: 8)
                            // The pulse: one ring per epoch, and nothing
                            // else on this screen moves.
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

                Button(role: model.isRecording ? .destructive : nil) {
                    if model.isRecording { model.stopRecording() }
                    else { model.startRecording(); elapsed = 0 }
                } label: {
                    Text(model.isRecording ? "Stop and close the file" : "Start a new file")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .controlSize(.large)

                // Offered rather than presented: a share sheet that appears
                // by itself the moment a file closes covers the row count
                // somebody was watching.
                if let url = model.fileToShare, !model.isRecording {
                    ShareLink(item: url) {
                        Label("Share \(url.lastPathComponent)", systemImage: "square.and.arrow.up")
                            .font(.footnote)
                    }
                }
            }
            .padding(.vertical, 4)
        }
    }

    private var positionSection: some View {
        Section {
            ValueRow(label: "Latitude", value: model.latest?.latitudeDeg.fixed(9, suffix: "°"))
            ValueRow(label: "Longitude", value: model.latest?.longitudeDeg.fixed(9, suffix: "°"))
            ValueRow(label: "Altitude", value: model.latest?.altitudeM.fixed(4, suffix: " m"))
            ValueRow(label: "Position RMS", value: model.latest?.posRmsM.fixed(4, suffix: " m"))
        } header: {
            HStack(spacing: 6) { Text("Position"); CodeChip(code: "PG") }
        }
    }

    private var velocitySection: some View {
        Section {
            ValueRow(label: "North", value: model.latest?.velNorthMps.fixed(4))
            ValueRow(label: "East", value: model.latest?.velEastMps.fixed(4))
            ValueRow(label: "Up", value: model.latest?.velUpMps.fixed(4))
            ValueRow(label: "Ground speed", value: model.latest?.velGroundMps.fixed(4, suffix: " m/s"))
        } header: {
            HStack(spacing: 6) { Text("Velocity"); CodeChip(code: "VG") }
        }
    }

    private var satellitesSection: some View {
        Section {
            ValueRow(label: "GPS", value: model.latest?.svGPS.map(String.init))
            ValueRow(label: "GLONASS", value: model.latest?.svGLONASS.map(String.init))
            ValueRow(label: "Galileo", value: model.latest?.svGalileo.map(String.init))
            ValueRow(label: "BeiDou", value: model.latest?.svBeiDou.map(String.init))
            ValueRow(label: "Total", value: model.latest?.svTotal.map(String.init))
        } header: {
            HStack(spacing: 6) { Text("Satellites"); CodeChip(code: "NP") }
        }
    }

    private var socketSection: some View {
        Section {
            ValueRow(label: "Throughput", value: (model.bytesPerSecond / 1024).fixed(1) + " kB/s")
            ValueRow(label: "Since last byte", value: model.secondsSinceLastByte.fixed(2) + " s")
        } header: {
            Text("Socket")
        } footer: {
            Text("A receiver that has gone quiet and a socket that has dropped look identical to the parser, so both are reported: the dot stops beating and the gap keeps climbing.")
        }
    }

    private var commandsSection: some View {
        Section {
            Text(commandText)
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
        } header: {
            Text("Sent to the receiver")
        } footer: {
            Text("`dm` again on Stop, so the receiver is not left streaming into a socket nobody is reading. Nothing else is touched: not PPP, not the corrections, not base or rover mode, and nothing under /par/net.")
        }
    }

    private var commandText: String {
        GreisCommands.startLogging(model.selection.requests).joined(separator: "\n")
    }

    private var formattedElapsed: String {
        let total = Int(elapsed)
        return String(format: "%02d:%02d:%02d", total / 3600, (total % 3600) / 60, total % 60)
    }
}
