# BLE Mesh Evaluation (Multi-client)

Date: 2026-03-02

## Goal
Improve latency and signal stability when multiple desktop clients are connected to one Android phone acting as Gemini proxy.

## Mesh feasibility in this project
- Not recommended for this architecture.
- Current stack is GATT (phone as peripheral/server, desktop as central/client).
- BLE Mesh is a different protocol family (managed flooding, provisioning, relay/friend nodes) and is not compatible with the current GATT framing without a full protocol rewrite.
- macOS/Windows desktop support for BLE Mesh as first-class app-level transport is limited compared to standard GATT workflows.
- Mesh would increase complexity, battery consumption, and likely end-to-end latency for request/response chat traffic.

## Decision
- Do not implement BLE Mesh in this codebase right now.
- Keep single-hop BLE GATT and optimize:
  - per-client send fairness/locking,
  - low-latency PHY preferences where supported,
  - reconnect resilience with jittered backoff,
  - direct auto-connect to known device.

## When to revisit Mesh
- Only if you need multi-hop coverage across large areas where phone<->desktop direct BLE is impossible.
- Even then, consider a dedicated relay architecture first (second Android relay or Wi-Fi fallback tunnel) before BLE Mesh.
