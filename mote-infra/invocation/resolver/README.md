# Invocation resolver

Reserved for explicit target-to-implementation resolution. For tool execution,
this resolver first supports the Kernel-to-local ingress; when Local Execution
takes the `local -> remote` branch it resolves that configured remote target.
Local Execution has already made the semantic local-versus-remote decision
before remote resolution. Resolution must not introduce hidden discovery,
fallback, mutable global registration, or a second tool-execution path.
