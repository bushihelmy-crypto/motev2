# Local invocation

Reserved for the concrete local transport/invocation implementation behind the
invocation contract. It delivers Invocation's ingress to
`mote-runtime/execution/local`; that package owns the semantic
local-versus-remote decision. This directory does not own Kernel state or
persistence transactions.
