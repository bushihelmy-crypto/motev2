# Complexity baseline review

The baseline records the completed owner consolidation and the single runtime
path:

```text
canonical typed frame
→ admission requirements reduction
→ model × protocol × service intersection
→ immutable admitted request
→ adapter
```

The neutral capability matrix, standalone plan, registries, raw DTO entries,
and alternate validation/dispatch paths are absent. Model, protocol, and
service keep their own facts; admission alone intersects them. Production model
data has one source: complete `ports.ModelCatalogSource` records converted into
an immutable Catalog and atomically refreshed.

| metric | original scaffold | previous review | current baseline |
| --- | ---: | ---: | ---: |
| production files | 83 | 64 | 64 |
| top-level declarations | 159 | 381 | 370 |
| function definitions | 53 | 189 | 186 |
| decision points | 146 | 489 | 475 |
| max cyclomatic | — | 11 | 11 |
| max nesting depth | — | 3 | 3 |
| import edges | 21 | 71 | 67 |
| package count | 35 | 31 | 31 |

The increase over the original scaffold is the reviewed cost of actual model
parameter and reasoning rules, complete-record validation, semantic request
reduction, exact service deployment checks, and all delivery modes sharing one
admission path. The package and file counts still fell because placeholder
packages and forwarding owners were removed.

The current baseline is lower than the previous review because production no
longer embeds or decodes a catalog seed and no longer merges invocation-time
model overrides. The checked-in gzip catalog is compiled only into model tests.
The public boundary also stopped using reflection to detect typed-nil interface
values; it now checks only missing dependencies under ordinary Go interface
semantics. Those ownership changes removed 11 declarations, 6 functions, 27
decision points, and 6 import edges without moving logic to an unmeasured path.

The latest review adds one public generation-parameter domain used by both the
v1 DTO and model catalog construction. Its 13 decision points reject invalid
temperature, top-p, output-token, and stop defaults or clamp boundaries before
a catalog becomes available; no adapter-time repair path was added. The public
composition boundary also now accepts only validated protocol and service
descriptors, while one adapter-free `Invocation` admission pipeline serves all
delivery modes. The net cost is 3 functions and 2 import edges. Maximum
cyclomatic complexity and nesting remain unchanged, and the old unary-bound
invocation types and constructors were deleted rather than retained as
compatibility wrappers.

The ratchet remains exact. Any production change must reproduce these metrics;
a real reduction lowers the baseline, while an increase requires a new
architecture review rather than a mechanical threshold bump.
