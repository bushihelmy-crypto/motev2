"""Graph-facing Think node contracts owned by the Think domain.

The Think flow is composed from the required Context, Compact, and Inference
nodes, followed by typed command interpretation.  Role activation injects the
narrow extension ports used by those nodes; the loop topology only composes the
selected nodes and never creates a second execution engine.

Context projections, compaction policy, model selection, and model providers
remain replaceable capabilities.  Their concrete implementations are supplied
through extensions while the required graph boundaries stay in this package.
"""
