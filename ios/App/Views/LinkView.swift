import SwiftUI
import GreisKit

/// One receiver, over TCP, on the Wi-Fi network it raises itself.
///
/// There is no port sweep here and no baud rate, because neither exists on
/// a socket. What is left is the identification, which never depended on
/// the transport: a verified GREIS checksum, or a reply to
/// `print,/par/rcv/model:on` from a link that stayed quiet.
struct LinkView: View {
    @Environment(AppModel.self) private var model
    @State private var isConnecting = false

    var body: some View {
        @Bindable var model = model

        NavigationStack {
            Form {
                Section {
                    TextField("Host", text: $model.link.host)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.numbersAndPunctuation)
                        .font(.system(.body, design: .monospaced))

                    LabeledContent("Port") {
                        TextField("8002", value: $model.link.port, format: .number.grouping(.never))
                            .keyboardType(.numberPad)
                            .multilineTextAlignment(.trailing)
                            .font(.system(.body, design: .monospaced))
                    }

                    SecureField("Password", text: $model.link.password)
                } header: {
                    Text("Address")
                } footer: {
                    Text("The receiver's own address in adhoc mode, and the TCP server port set with `set,/par/net/tcp/port,8002`. The password guards TCP and FTP together; leave it empty if the receiver has none.")
                }

                Section {
                    Button {
                        Task {
                            isConnecting = true
                            await model.connectAndIdentify()
                            isConnecting = false
                        }
                    } label: {
                        HStack {
                            Text("Connect and identify")
                            Spacer()
                            if isConnecting { ProgressView() }
                        }
                    }
                    .disabled(isConnecting || model.isRecording)

                    stateRow
                } footer: {
                    Text("A verified checksum is proof rather than a guess: the header, the body length and every byte between them have to be right for it to come out. A link that stays quiet through the listening window is asked what it is instead — a receiver answers, an idle socket does not.")
                }

                Section {
                    Toggle("Replay a recorded stream", isOn: $model.link.useReplay)
                        .tint(.accentColor)
                } header: {
                    Text("Without a receiver")
                } footer: {
                    Text("Plays `sample-stream.bin` from the app bundle instead of opening a socket, so every screen and the CSV writer can be exercised in the Simulator with nothing plugged in. No commands are sent — there is nothing on the other end to obey them.")
                }

                Section {
                    Text("The receiver's access point has no internet. iOS will say so when you join it, may offer to leave, and will prefer cellular for anything it can — so this connection is pinned to Wi-Fi.")
                    Text("The first socket to a device on your network triggers the Local Network prompt. Denied once, it can only be re-granted in Settings.")
                } header: {
                    Text("Two things iOS will do")
                }
                .font(.footnote)
                .foregroundStyle(.secondary)
            }
            // A Form paints its own scroll background, so it has to be
            // hidden before the ground behind it can show.
            .scrollContentBackground(.hidden)
            .background(.ground)
            .navigationTitle("Link")
        }
    }

    @ViewBuilder
    private var stateRow: some View {
        switch model.linkState {
        case .idle:
            LabeledContent("Status") { Text("Not connected").foregroundStyle(.secondary) }
        case .connecting(let stage):
            LabeledContent("Status") {
                Text(stage).font(.system(.footnote, design: .monospaced)).foregroundStyle(.secondary)
            }
        case .connected(let name):
            LabeledContent("Receiver") {
                Text(name ?? "Answered GREIS")
                    .font(.system(.body, design: .monospaced))
            }
        case .failed(let message):
            VStack(alignment: .leading, spacing: 4) {
                Text("Could not connect").font(.callout.weight(.semibold))
                Text(message).font(.footnote).foregroundStyle(.secondary)
            }
        }
    }
}
