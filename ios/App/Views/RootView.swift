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

/// The ground the cards sit on.
///
/// Not `systemGroupedBackground`, which in dark appearance is pure black.
/// Pure black is a colour choice rather than the absence of one, and it
/// flattens the step between the page and the cards on it into a hard
/// edge. #141417 is the same value the desktop palette uses, so the two
/// applications are the same shade rather than nearly.
extension ShapeStyle where Self == Color {
    static var ground: Color { Color("Ground") }
}

/// A content card with a corner radius this app chooses, rather than the
/// one a grouped `List` section imposes.
///
/// `Form` and `List` are right for the settings screens - they *are*
/// settings - but the recording screen is a readout, and its rounding was
/// visibly tighter than the design called for. A radius is not something
/// a list style exposes, so the readout stops being a list.
///
/// `.continuous` matters as much as the number: a circular corner meets
/// the straight edge with a visible break, and every rounded rectangle
/// Apple draws is a squircle.
struct Card<Content: View>: View {
    var radius: CGFloat = 28
    var glass: Bool = false
    @ViewBuilder var content: Content

    private var shape: RoundedRectangle {
        RoundedRectangle(cornerRadius: radius, style: .continuous)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) { content }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(18)
            .modifier(CardSurface(shape: shape, glass: glass))
            .padding(.horizontal, 14)
    }
}

private struct CardSurface: ViewModifier {
    let shape: RoundedRectangle
    let glass: Bool

    func body(content: Content) -> some View {
        if glass {
            // Chrome, so it is glass. Content cards below stay opaque:
            // stacking translucency on translucency is what turns a
            // readable screen into soup.
            content.glassBackground(in: shape)
        } else {
            content
                .background(Color(.secondarySystemGroupedBackground), in: shape)
                .shadow(color: .black.opacity(0.06), radius: 10, y: 3)
        }
    }
}

/// A section title above a card, in the place a grouped list would put it.
struct CardHeader: View {
    let title: String
    var code: String? = nil

    var body: some View {
        HStack(spacing: 6) {
            Text(title.uppercased())
                .font(.caption)
                .tracking(0.4)
                .foregroundStyle(.secondary)
            if let code { CodeChip(code: code) }
        }
        .padding(.horizontal, 30)
        .padding(.top, 22)
        .padding(.bottom, 7)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// Explanatory text under a card, same role as a list section footer.
struct CardFooter: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.footnote)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 30)
            .padding(.top, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// One label/value line inside a `Card`, with the hairline above it that a
/// list row would have drawn.
struct CardRow<Trailing: View>: View {
    let label: String
    var first: Bool = false
    @ViewBuilder var trailing: Trailing

    var body: some View {
        VStack(spacing: 0) {
            if !first {
                Divider().padding(.vertical, 10)
            }
            HStack(alignment: .firstTextBaseline) {
                Text(label).foregroundStyle(.secondary)
                Spacer(minLength: 12)
                trailing
            }
        }
    }
}

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
