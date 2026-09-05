import SwiftUI

/// The one place in the app that knows about Liquid Glass.
///
/// This file is deliberately the only thing that touches the iOS 26 glass
/// API, because it was written without an SDK to compile against. The
/// default path uses `.ultraThinMaterial`, which has existed since iOS 15
/// and is certain to build; the glass path is behind a compile flag.
///
/// **To turn glass on:** add `USE_LIQUID_GLASS` to *Swift Compiler –
/// Custom Flags → Active Compilation Conditions* in the app target (or
/// uncomment the line in `project.yml`), build, and fix this one file if
/// the signature has moved. Nothing else in the app refers to the API, so a
/// rename costs a single edit rather than a sweep through every view.
struct GlassBackground<S: InsettableShape>: ViewModifier {
    let shape: S
    var interactive: Bool = false

    func body(content: Content) -> some View {
        #if USE_LIQUID_GLASS
        if #available(iOS 26.0, *) {
            content.glassEffect(interactive ? .regular.interactive() : .regular, in: shape)
        } else {
            content.background(.ultraThinMaterial, in: shape)
        }
        #else
        content
            .background(.ultraThinMaterial, in: shape)
            .overlay(
                // The specular top edge. Glass reads as a thick material
                // because of the light caught on its lip, not because of
                // the blur — the blur alone looks like frosted plastic.
                shape.strokeBorder(
                    LinearGradient(
                        colors: [.white.opacity(0.5), .white.opacity(0.08)],
                        startPoint: .top, endPoint: .bottom
                    ),
                    lineWidth: 0.75
                )
            )
        #endif
    }
}

extension View {
    func glassBackground<S: InsettableShape>(in shape: S, interactive: Bool = false) -> some View {
        modifier(GlassBackground(shape: shape, interactive: interactive))
    }
}

/// Apple parameterises a spring as overshoot plus response rather than as
/// mass, stiffness and damping. These are the two the app uses.
extension Animation {
    /// Critically damped: graceful, non-distracting, no overshoot. The
    /// default for anything that was not thrown.
    static let settle = Animation.spring(response: 0.35, dampingFraction: 1.0)
    /// A little bounce, earned only when the gesture carried momentum.
    static let momentum = Animation.spring(response: 0.35, dampingFraction: 0.8)
}
