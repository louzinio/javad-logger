// swift-tools-version: 6.0
import PackageDescription

// GreisKit is everything that is not a screen: the checksum, the framing,
// the message layouts, the carry-forward state, the CSV writer and the two
// transports. It builds and tests on macOS on its own, with no simulator
// and no receiver, which is the only part of this project that can be
// verified without hardware.
let package = Package(
    name: "GreisKit",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "GreisKit", targets: ["GreisKit"])
    ],
    targets: [
        .target(name: "GreisKit"),
        .testTarget(name: "GreisKitTests", dependencies: ["GreisKit"])
    ]
)
