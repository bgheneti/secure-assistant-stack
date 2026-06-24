# How this relates to similar projects

This stack shares the agent-security threat model explored by [nono](https://github.com/always-further/nono), [bromure](https://github.com/rderaison/bromure), and [OneCLI's own tooling](https://github.com/onecli/onecli): isolate the agent, keep secrets out of it, scope how those secrets get used, and watch the supply chain. The difference is scope and shape:

- **nono** is a zero-setup kernel sandbox you wrap around a single agent. This stack is a **full multi-tier deployment** (vault + router + TEE + N agents) rather than a per-agent wrapper.
- **bromure** sandboxes agents in disposable macOS VMs with a host-side MITM proxy. Same "swap fake creds for real ones on the wire" idea (we use OneCLI for that), but this stack targets Linux VMs/hosts and adds tiered identities + confidential inference.
- **OneCLI / ZeroClaw** are the building blocks — this repo is the opinionated glue that runs them together, fail-closed, behind one allowlist.

If you want per-agent sandboxing on a laptop, reach for nono or bromure. If you want a self-hosted, always-on, multi-identity agent deployment with no raw credentials and no unfiltered egress, this is that.

---

> Back to the [README](../README.md).
