import SwiftUI

/// One file per session, and the header says what is in it.
struct FilesView: View {
    @Environment(AppModel.self) private var model
    @State private var refresh = UUID()

    var body: some View {
        NavigationStack {
            List {
                if model.files.isEmpty {
                    ContentUnavailableView(
                        "No files yet",
                        systemImage: "doc.text",
                        description: Text("A session writes one CSV here, with a column per field of each message you ticked.")
                    )
                } else {
                    ForEach(model.files, id: \.self) { url in
                        FileRow(url: url)
                    }
                    .onDelete { offsets in
                        for index in offsets { model.delete(model.files[index]) }
                        refresh = UUID()
                    }
                }
            }
            .id(refresh)
            .navigationTitle("Files")
            .toolbar { EditButton() }
            .refreshable { refresh = UUID() }
            .overlay(alignment: .bottom) {
                Text("An empty cell means “not reported”, never zero. A missing velocity component is not a stationary receiver.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding()
            }
        }
    }
}

private struct FileRow: View {
    let url: URL

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 3) {
                Text(url.lastPathComponent)
                    .font(.system(.subheadline, design: .monospaced))
                Text(subtitle)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            ShareLink(item: url) { Image(systemName: "square.and.arrow.up") }
                .labelStyle(.iconOnly)
                .buttonStyle(.borderless)
        }
    }

    private var subtitle: String {
        let values = try? url.resourceValues(forKeys: [.fileSizeKey, .contentModificationDateKey])
        let size = values?.fileSize ?? 0
        let modified = values?.contentModificationDate ?? Date()
        return "\(ByteCountFormatter.string(fromByteCount: Int64(size), countStyle: .file)) · \(modified.formatted(date: .abbreviated, time: .shortened))"
    }
}
