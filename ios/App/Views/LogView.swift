import SwiftUI
import GreisKit

/// What to log, and how often. Every ticked message adds its own columns to
/// the file, at its own period.
struct LogView: View {
    @Environment(AppModel.self) private var model
    var startRecording: () -> Void

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    ForEach(Catalog.all) { message in
                        MessageRow(message: message)
                    }
                } header: {
                    Text("Messages")
                } footer: {
                    Text("Position cannot be switched off: it is the message that closes an epoch, so without it there is no moment at which a row becomes complete. A slower message leaves no holes — its last value is carried forward onto every row until the next one arrives.")
                }

                Section {
                    LabeledContent("Saved to") {
                        Text("On My iPhone › Javad Logger").foregroundStyle(.secondary)
                    }
                    Toggle("Share when the file closes", isOn: Binding(
                        get: { model.shareWhenFinished },
                        set: { model.shareWhenFinished = $0 }
                    ))
                } header: {
                    Text("Where it goes")
                } footer: {
                    Text("iOS gives no free filesystem, so there is no folder to pick: files land in the app's own container, which the Files app shows. \(columnSummary)")
                }

                Section {
                    Button {
                        model.startRecording()
                        startRecording()
                    } label: {
                        Text("Start recording").frame(maxWidth: .infinity)
                    }
                    .disabled(model.isRecording)
                }
            }
            .navigationTitle("Log")
        }
    }

    private var columnSummary: String {
        let total = model.selection.columnCount
        let count = model.selection.messages.count
        return "host_time_utc first and always, then \(total - 1) columns from the \(count) message\(count == 1 ? "" : "s") selected."
    }
}

private struct MessageRow: View {
    @Environment(AppModel.self) private var model
    let message: LogMessage

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Toggle(isOn: binding) {
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Text(message.label)
                        CodeChip(code: message.code)
                    }
                    Text(message.detail)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .disabled(message.mandatory)

            // A derived entry has no rate to choose: it is computed from a
            // message that is already arriving, so it arrives exactly as
            // often as that one does. Offering a picker here would offer a
            // decision with no effect.
            if !message.derived {
                Picker("Rate", selection: periodBinding) {
                    ForEach(Catalog.periods, id: \.self) { period in
                        Text(Catalog.periodLabel(period)).tag(period)
                    }
                }
                .pickerStyle(.menu)
                .disabled(!model.selection.isOn(message))
            }
        }
        .padding(.vertical, 2)
    }

    private var binding: Binding<Bool> {
        Binding(
            get: { model.selection.isOn(message) },
            set: { isOn in
                guard !message.mandatory else { return }
                var selection = model.selection
                if isOn { selection.enabled.insert(message.code) }
                else { selection.enabled.remove(message.code) }
                model.selection = selection
            }
        )
    }

    private var periodBinding: Binding<Double> {
        Binding(
            get: { model.selection.period(message) },
            set: { period in
                var selection = model.selection
                selection.periods[message.code] = period
                model.selection = selection
            }
        )
    }
}
