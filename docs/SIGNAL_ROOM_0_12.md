# Signal Room: interface research and decisions

Research checked 29 October 2026. This is an offline product design direction, not a claim that experimental research has become an industry standard.

## What changed in the design landscape

[Microsoft's agent UX principles](https://microsoft.design/articles/ux-design-for-agents/) extend interaction beyond a conversational transcript and emphasize visible behavior, human control and context. The [HAX design library](https://www.microsoft.com/en-us/haxtoolkit/library/) provides more established guidance for efficient invocation, dismissal, correction and global controls. Ariadne should reveal actual work and the owner's decisions as primary objects.

The September 2026 [Blazor Agentic UI announcement](https://devblogs.microsoft.com/dotnet/build-agentic-ui-blazor/) describes experimental components that expose agent content and lifecycle state to application-controlled views. The useful architectural principle is separating observable work state from its visual presentation. It does not require adopting .NET or adding a new framework to Ariadne.

[Google Research's 2026 adaptive generative interfaces proposal](https://research.google/pubs/self-evolving-systems-moving-beyond-deterministic-interfaces-to-adaptive-generative-interfaces/) explores interfaces that grow or reorganize around intent. That is a research direction, not proof that arbitrary generated interfaces are reliable or cheap. Ariadne can provide contextual surfaces using its existing trusted state without asking a model to regenerate the interface, executing generated UI code or consuming inference while idle.

[WCAG 2.2](https://www.w3.org/TR/WCAG22/) remains the relevant published accessibility baseline. It addresses contrast, reflow, keyboard interaction, unobscured focus and minimum pointer targets. The [target-size explanation](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum) describes the 24 CSS-pixel minimum and its exceptions; our primary controls should be comfortably larger. A futuristic appearance does not itself establish accessibility conformance.

## Product interpretation

| Design choice | Purpose | Operating cost and limits |
| --- | --- | --- |
| Spatial signal room with a central thread structure | Make Ariadne feel present while keeping the work and state readable | Native HTML/CSS/SVG, no new resident model, framework, remote font or GPU scene |
| State-driven presence | Visually distinguish standing by, executing, review, verification, paused and unavailable states | Uses existing status and mission data; decoration never claims cognition, microphone activity or network health |
| Contextual disclosure | Keep less-used system configuration reachable without surrounding the primary task with settings | Existing local controls and keyboard paths remain usable; no model calls to choose a layout |
| Explicit microphone and web indicators | Keep owner authority legible even when visual composition changes | Listening remains off after the owner's stop request until deliberately enabled |
| A portrait editorial daily brief | Make weather, regional impact and recorded activity readable in one glance and easy to share deliberately | Bounded deterministic layout from dated report facts; full evidence remains available separately |
| A desktop-independent local host | Keep the interface and scheduled local work available after the coding app closes | Fixed local Windows process lifecycle, no required hosted service or recurring API subscription |

## Boundaries and next experiments

The current work does not add gesture tracking, camera monitoring, autonomous UI code generation, a self-editing permission model or always-running 3D rendering. Those would each require their own useful task, authority model and measured resource budget.

Potential later experiments are an owner-selected focus surface for the current artifact, a compact review queue that expands on demand, and direct manipulation of a bounded workflow graph. They should first be tested against the existing interface for task completion, error recovery and owner understanding. Speculative novelty alone is not evidence of better UX.

Verification distinguishes source/handler checks from live visual, keyboard and responsive checks. No blanket WCAG certification or universal usability improvement is claimed.
