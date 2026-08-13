import ast
from collections.abc import Iterator
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "mote_kernel"


def _production_modules() -> Iterator[tuple[str, ast.Module]]:
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        relative = path.relative_to(PACKAGE_ROOT).as_posix()
        yield relative, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _module(relative: str) -> ast.Module:
    path = PACKAGE_ROOT / relative
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _top_level_definition(relative: str, name: str) -> ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef:
    return next(
        node
        for node in _module(relative).body
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name
    )


def _defined_names(tree: ast.Module) -> frozenset[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return frozenset(names)


def _symbol_owners(symbols: frozenset[str]) -> dict[str, tuple[str, ...]]:
    owners: dict[str, list[str]] = {symbol: [] for symbol in symbols}
    for relative, tree in _production_modules():
        for symbol in symbols & _defined_names(tree):
            owners[symbol].append(relative)
    return {symbol: tuple(paths) for symbol, paths in owners.items()}


def _class_fields(relative: str, name: str) -> dict[str, str]:
    definition = _top_level_definition(relative, name)
    if not isinstance(definition, ast.ClassDef):
        raise AssertionError(f"{relative}:{name} is not a class")
    return {
        statement.target.id: ast.unparse(statement.annotation)
        for statement in definition.body
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
    }


def _call_owner_modules(call_name: str) -> tuple[str, ...]:
    owners: list[str] = []
    for relative, tree in _production_modules():
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name | ast.Attribute)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == call_name)
                or (isinstance(node.func, ast.Attribute) and node.func.attr == call_name)
            )
            for node in ast.walk(tree)
        ):
            owners.append(relative)
    return tuple(owners)


def test_graph_state_and_execution_contracts_have_single_owners() -> None:
    owners_by_module = {
        "state/graph_state/identity.py": frozenset(
            {
                "GraphRunId",
                "GraphDefinitionId",
                "GraphDefinitionVersion",
                "GraphNodeId",
                "GraphRouteId",
                "GraphExecutionAttemptId",
                "GraphInterruptId",
                "graph_interrupt_id",
                "child_graph_run_id",
            }
        ),
        "state/graph_state/routing.py": frozenset(
            {"ContinueGraphRouting", "SelectGraphRoute", "GraphRoutingContribution"}
        ),
        "state/graph_state/frontier_model.py": frozenset(
            {
                "GraphFailure",
                "GraphInterruptPayload",
                "GraphResumeInputPayload",
                "GraphResumeInputCodecId",
                "GraphSkipReason",
                "GraphResumeInputCodec",
                "UseStepRequestInput",
                "OverrideGraphNodeInput",
                "GraphNodeInputBinding",
                "PendingGraphNode",
                "SucceededGraphNode",
                "FailedGraphNode",
                "GraphNodeInterruptIdentity",
                "GraphNodeInterrupt",
                "InterruptedGraphNode",
                "SkippedGraphNode",
                "GraphNodeSettlement",
                "GraphFrontierNode",
                "GraphFrontierState",
            }
        ),
        "state/graph_state/command.py": frozenset(
            {
                "SucceededGraphNodeOutcome",
                "FailedGraphNodeOutcome",
                "InterruptedGraphNodeOutcome",
                "GraphNodeOutcome",
                "AdvanceGraphFrontier",
                "CompleteGraphFrontier",
                "GraphFrontierResolution",
                "ResumeFailedNode",
                "SkipFailedNode",
                "ResumeInterruptedNode",
                "GraphNodeResumeAction",
                "SettleGraphExecution",
                "ResumeGraphNodes",
            }
        ),
        "state/graph_state/reducer.py": frozenset({"reduce_graph_run"}),
        "execution/graph/resume_input.py": frozenset(
            {"ResumeInputEncoder", "ResumeInputDecoder", "ResumeInputBinding"}
        ),
        "execution/engine/resume_input.py": frozenset(
            {"require_resume_input_binding", "encode_resume_input", "effective_node_input"}
        ),
        "execution/engine/routing.py": frozenset({"validate_routing_contribution", "resolve_routing"}),
        "execution/engine/task.py": frozenset({"TaskId", "task_identity", "GraphTask", "ExecutableTask"}),
        "execution/identity.py": frozenset({"ExecutionRequestAttemptId"}),
        "execution/claim.py": frozenset(
            {"ExecutionClaimOwner", "ExecutionClaimSnapshot", "PreparedExecutionClaim", "prepare_execution_claim"}
        ),
        "execution/graph_run.py": frozenset({"project_start_graph_command"}),
        "execution/executor.py": frozenset({"GraphExecutor"}),
    }
    expected = {symbol: (relative,) for relative, symbols in owners_by_module.items() for symbol in symbols}

    assert _symbol_owners(frozenset(expected)) == expected


def test_static_execution_and_resource_types_reuse_state_owned_identities() -> None:
    assert _class_fields("state/graph_state/model.py", "GraphExecutionLease")["node_ids"] == "tuple[GraphNodeId, ...]"
    assert _class_fields("state/graph_state/model.py", "ParentGraphActivation") == {
        "run_id": "GraphRunId",
        "superstep": "int",
        "node_id": "GraphNodeId",
    }
    assert _class_fields("state/graph_state/resource_model.py", "ResourceLock") == {
        "resource_id": "ResourceId",
        "owner": "GraphNodeId | None",
        "waiters": "tuple[GraphNodeId, ...]",
    }
    assert _class_fields("state/graph_state/resource_model.py", "ResourceAcquisition")["node_id"] == "GraphNodeId"
    assert _class_fields("execution/graph/edge.py", "DirectEdge") == {
        "source": "GraphNodeId",
        "target": "GraphNodeId",
    }
    assert _class_fields("execution/graph/edge.py", "ConditionalEdge") == {
        "source": "GraphNodeId",
        "route": "GraphRouteId",
        "target": "GraphNodeId",
    }
    assert _class_fields("execution/graph/outcome.py", "NodeSuccess")["routing"] == "GraphRoutingContribution"
    assert _class_fields("execution/claim.py", "ExecutionClaimSnapshot") == {
        "command": "ClaimGraphExecution",
        "token": "GraphExecutionToken",
        "node_ids": "tuple[GraphNodeId, ...]",
        "task_ids": "tuple[TaskId, ...]",
        "request_attempt_id": "ExecutionRequestAttemptId",
    }

    resource_tree = _module("state/graph_state/resource_model.py")
    resource_newtypes = {
        target.id
        for node in resource_tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "NewType"
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert resource_newtypes == {"ResourceId"}


def test_graph_state_does_not_process_compiled_or_generic_inputs() -> None:
    violations: list[str] = []
    state_root = PACKAGE_ROOT / "state" / "graph_state"
    for path in sorted(state_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(PACKAGE_ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in {"CompiledGraph", "InputT", "TaskId"}:
                violations.append(f"{relative}:{node.lineno} uses {node.id}")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr
                in {
                    "encode",
                    "decode",
                }
            ):
                violations.append(f"{relative}:{node.lineno} calls {node.func.attr}")
    assert not violations, (
        f"graph state must not own compiled topology, task identity, or input processing: {violations}"
    )


def test_resume_codec_is_invoked_only_by_its_node_input_materializer() -> None:
    invocation_owners: list[tuple[str, str]] = []
    for relative, tree in _production_modules():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            receiver = node.func.value
            if (node.func.attr == "encode" and isinstance(receiver, ast.Attribute) and receiver.attr == "encoder") or (
                node.func.attr == "decode" and isinstance(receiver, ast.Attribute) and receiver.attr == "decoder"
            ):
                invocation_owners.append((relative, node.func.attr))

    assert sorted(invocation_owners) == [
        ("execution/engine/resume_input.py", "decode"),
        ("execution/engine/resume_input.py", "encode"),
    ]


def test_compiled_routing_is_interpreted_only_by_routing_and_snapshot_guard() -> None:
    owners: dict[str, set[str]] = {
        "direct_targets": set(),
        "conditional_targets": set(),
        "joins_by_source": set(),
    }
    for relative, tree in _production_modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in owners:
                owners[node.attr].add(relative)

    assert owners == {
        "direct_targets": {"execution/engine/routing.py"},
        "conditional_targets": {"execution/engine/routing.py"},
        "joins_by_source": {"execution/engine/routing.py", "execution/engine/snapshot_guard.py"},
    }


def test_node_invocation_belongs_to_the_single_execution_scheduler() -> None:
    assert _call_owner_modules("node") == ("execution/engine/scheduler.py",)


def test_execution_requests_read_authoritative_graph_state() -> None:
    assert _class_fields("execution/request.py", "StepRequest")["state"] == "GraphRunState"
    assert _class_fields("execution/request.py", "ResumeRequest")["state"] == "GraphRunState"


def test_executor_does_not_apply_state_or_own_persistence() -> None:
    tree = _module("execution/executor.py")
    graph_executor = _top_level_definition("execution/executor.py", "GraphExecutor")
    if not isinstance(graph_executor, ast.ClassDef):
        raise AssertionError("GraphExecutor must remain a class")
    slots = next(
        node.value
        for node in graph_executor.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__slots__" for target in node.targets)
    )
    assert isinstance(slots, ast.Tuple)
    assert {element.value for element in slots.elts if isinstance(element, ast.Constant)} == {
        "_claim_owner",
        "_graphs",
        "_parent_nodes",
        "_root_key",
    }

    forbidden_names = {"reduce_graph_run", "replace", "store", "state_store"}
    assert not {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id in forbidden_names}
    assert not {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr in forbidden_names
    }
    assert not any(
        isinstance(node, ast.ImportFrom) and node.module is not None and "reducer" in node.module
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "state"
        for node in ast.walk(tree)
        for target in (
            node.targets
            if isinstance(node, ast.Assign)
            else (node.target,)
            if isinstance(node, ast.AnnAssign | ast.AugAssign)
            else ()
        )
    )


def test_graph_transition_dispatch_is_exhaustive_and_modules_do_not_alias_contracts() -> None:
    reducer = _top_level_definition("state/graph_state/reducer.py", "reduce_graph_run")
    assert any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "assert_never"
        for node in ast.walk(reducer)
    )
    assert not any(isinstance(node, ast.Try) for node in ast.walk(reducer))

    aliases: list[str] = []
    for relative, tree in _production_modules():
        if not relative.startswith(("state/graph_state/", "execution/")):
            continue
        for node in tree.body:
            if relative.endswith("/__init__.py") and isinstance(node, ast.Import | ast.ImportFrom):
                aliases.extend(
                    f"{relative}:{node.lineno} imports {alias.name} as {alias.asname}"
                    for alias in node.names
                    if alias.asname is not None and alias.asname != alias.name
                )
            elif (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Name)
                and any(isinstance(target, ast.Name) for target in node.targets)
            ):
                aliases.append(f"{relative}:{node.lineno} defines a forwarding alias")
    assert not aliases, f"graph contracts must not gain forwarding aliases: {aliases}"


def test_graph_runtime_has_no_forwarding_only_compatibility_modules() -> None:
    violations: list[str] = []
    for relative, tree in _production_modules():
        if relative.endswith("/__init__.py") or not relative.startswith(("state/graph_state/", "execution/")):
            continue
        owned_names = _defined_names(tree) - {"__all__"}
        if not owned_names:
            violations.append(relative)
    assert not violations, (
        f"graph runtime modules must own behavior or values instead of forwarding imports: {violations}"
    )


def test_graph_run_lifecycle_has_exactly_three_current_states() -> None:
    status = _top_level_definition("state/graph_state/model.py", "GraphRunStatus")
    assert isinstance(status, ast.ClassDef)
    members = {
        target.id
        for statement in status.body
        if isinstance(statement, ast.Assign)
        for target in statement.targets
        if isinstance(target, ast.Name)
    }
    assert members == {"RUNNING", "COMPLETED", "ABORTED"}
