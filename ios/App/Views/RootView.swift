import SwiftUI

/// Four decisions, one per tab, in the order they are made: which receiver,
/// what to log, watch it arrive, take the file.
struct RootView: View {
    @Environment(AppModel.self) private var model
    @State private var tab = Tab.record

    enum Tab: Hashable { case link, log, record, files }

    var body: some View {
        TabView(selection: $tab) {
            LinkView()
                .tabItem { Label("Link", systemImage: "wifi") }
                .tag(Tab.link)

            LogView(startRecording: { tab = .record })
                .tabItem { Label("Log", systemImage: "checklist") }
                .tag(Tab.log)

            RecordView()
                .tabItem { Label("Record", systemImage: "smallcircle.filled.circle") }
                .tag(Tab.record)

            FilesView()
                .tabItem { Label("Files", systemImage: "doc.text") }
                .tag(Tab.files)
        }
    }
}

// MARK: - Shared pieces

/// A value that lines up with the ones above and below it. Digits are the
/// content of this app, so they get a monospaced face and tabular figures
/// everywhere they appear.
struct ValueRow: View {
    let label: String
    let value: String?

    var body: some View {
        LabeledContent {
            Text(value ?? "—")
                .font(.system(.subheadline, design: .monospaced))
                .monospacedDigit()
                .foregroundStyle(value == nil ? .tertiary : .primary)
        } label: {
            Text(label)
        }
    }
}

/// A GREIS message id, shown next to the human name so the two are never
/// confused with each other in a bug report.
struct CodeChip: View {
    let code: String

    var body: some View {
        Text("[\(code)]")
            .font(.system(.caption2, design: .monospaced))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 5)
            .padding(.vertical, 1.5)
            .background(.quaternary, in: RoundedRectangle(cornerRadius: 5))
    }
}

extension Double {
    func fixed(_ places: Int) -> String { String(format: "%.\(places)f", self) }
}

extension Optional where Wrapped == Double {
    func fixed(_ places: Int, suffix: String = "") -> String? {
        self.map { $0.fixed(places) + suffix }
    }
}
