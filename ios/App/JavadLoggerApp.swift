import SwiftUI

@main
struct JavadLoggerApp: App {

    @State private var model = AppModel()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(model)
                .task {
                    model.load()
                    model.refreshFiles()
                }
        }
        .onChange(of: scenePhase) { _, phase in
            // Foreground only, and the app says so rather than failing
            // quietly: iOS suspends a backgrounded app and closes its
            // sockets, which would end the file mid-row.
            if phase == .background { model.applicationDidEnterBackground() }
        }
    }
}
