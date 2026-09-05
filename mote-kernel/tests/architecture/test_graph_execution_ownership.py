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
                "StartGraphRun",
                "ClaimGraphExecution",
                "FenceGraphExecution",
                "SettleGraphNode",
                "AdvanceGraphFrontier",
                "CompleteGraphFrontier",
                "ResumeInterruptedNode",
                "GraphNodeResumeAction",
                "ResumeGraphNodes",
                "AbortGraphRun",
            }
        ),
        "state/graph_state/model.py": frozenset({"GraphExecutionToken", "GraphExecutionLease", "GraphRunState"}),
        "state/graph_state/reducer.py": frozenset({"reduce_graph_run"}),
        "execution/commit.py": frozenset(
            {
                "GraphCommitKey",
                "GraphCommitWriteSet",
                "GraphTransition",
                "GraphCommit",
                "prepare_transition",
                "confirm_transition",
                "commit_transition",
                "apply_commit_writes",
            }
        ),
        "execution/graph/resume_input.py": frozenset(
            {"ResumeInputEncoder", "ResumeInputDecoder", "ResumeInputBinding"}
        ),
        "execution/engine/resume_input.py": frozenset(
            {
                "require_resume_input_binding",
                "encode_resume_input",
                "decode_resume_input",
                "materialize_node_input",
            }
        ),
        "execution/graph/values.py": frozenset(
            {
                "NamedValue",
                "_ValuesSeal",
                "_ValuesConstruction",
                "_GraphValues",
                "GraphInputFrame",
                "NodeInputFrame",
                "NodeOutputFrame",
                "GraphOutputView",
                "_make_graph_values",
            }
        ),
        "execution/run_context.py": frozenset(
            {
                "GraphInputAvailabilityCoordinate",
                "PublicationAvailabilityCoordinate",
                "ResumeInputAvailabilityCoordinate",
                "ChildBoundaryAvailabilityCoordinate",
                "GraphInputEvidence",
                "GraphPublicationEvidence",
                "ScopedFrameIndex",
                "_GraphContinuation",
            }
        ),
        "execution/engine/routing.py": frozenset(
            {
                "PublicationHistoryWindow",
                "publication_history_window",
                "validate_routing_contribution",
                "resolve_routing",
            }
        ),
        "execution/engine/resume_admission.py": frozenset({"prepare_resume"}),
        "execution/engine/task.py": frozenset({"TaskId", "task_identity", "GraphTask", "ExecutableTask"}),
        "execution/identity.py": frozenset({"ScopeRunCoordinate", "StableActivation", "stable_activation"}),
        "execution/claim.py": frozenset(
            {
                "ExecutionClaimOwner",
                "PreparedExecutionClaim",
            }
        ),
        "execution/engine/frontier.py": frozenset({"FrontierPreparation"}),
        "execution/engine/superstep.py": frozenset({"ExecutableFrontier", "PrepareDisposition"}),
        "execution/engine/scheduler.py": frozenset({"TaskRaised", "TaskScheduler"}),
        "execution/engine/session.py": frozenset({"GraphExecutionSession"}),
        "execution/graph_run.py": frozenset({"project_start_graph_command"}),
        "execution/executor.py": frozenset({"GraphExecutor"}),
        "execution/facade.py": frozenset({"Graph"}),
    }
    expected = {symbol: (relative,) for relative, symbols in owners_by_module.items() for symbol in symbols}

    assert _symbol_owners(frozenset(expected)) == expected


def test_static_execution_and_resource_types_reuse_state_owned_identities() -> None:
    assert _class_fields("state/graph_state/model.py", "GraphExecutionLease") == {"token": "GraphExecutionToken"}
    assert _class_fields("state/graph_state/identity.py", "GraphActivationIdentity") == {
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
    assert _class_fields("execution/graph/outcome.py", "_GraphSuccessOutcome") == {
        "output": "_GraphValues[GraphValueT]",
        "route": "str | None",
        "_seal": "InitVar[_OutcomeSeal]",
    }


def test_production_continuation_has_no_hidden_mutation_path() -> None:
    source = (PACKAGE_ROOT / "execution" / "run_context.py").read_text()

    assert "object.__setattr__" not in source
    assert "def checkpoint(" not in source
    assert "_checkpoint_continuation" not in source


def test_resource_state_identities_are_owned_by_the_durable_model() -> None:

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
        "joins_by_source": {"execution/engine/routing.py"},
    }
    recovery = _module("execution/engine/recovery.py")
    forbidden = {"materializations", "graph_outputs"}
    assert not {node.attr for node in ast.walk(recovery) if isinstance(node, ast.Attribute) and node.attr in forbidden}
    assert not {node.id for node in ast.walk(recovery) if isinstance(node, ast.Name) and node.id in forbidden}


def test_node_invocation_belongs_to_the_single_execution_scheduler() -> None:
    assert _call_owner_modules("operation") == ("execution/engine/scheduler.py",)


def test_resume_input_and_confirmed_values_share_the_single_scoped_frame_index() -> None:
    availability = _top_level_definition("execution/run_context.py", "ScopedFrameAvailability")
    assert isinstance(availability, ast.ClassDef)
    methods = {node.name for node in availability.body if isinstance(node, ast.FunctionDef)}
    assert methods == {"has_graph_input", "has_publication", "has_resume_input", "has_child_boundary"}
    assert _class_fields("execution/run_context.py", "ScopedFrameIndex") == {
        "graph_inputs": "tuple[AdmittedGraphInput[GraphValueT], ...]",
        "publications": "tuple[ConfirmedPublication[GraphValueT], ...]",
        "resume_inputs": "tuple[AdmittedResumeInput[GraphValueT], ...]",
        "child_boundaries": "tuple[ConfirmedChildBoundary[GraphValueT], ...]",
    }


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
        "_graph",
    }
    assert {
        node.name
        for node in graph_executor.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and not node.name.startswith("_")
    } == {"issue_session", "prepare"}

    forbidden_names = {"reduce_graph_run", "store", "state_store"}
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


def test_child_handle_exposes_only_named_invocation_capabilities() -> None:
    handle = _top_level_definition("execution/family_driver.py", "_ChildHandle")
    if not isinstance(handle, ast.ClassDef):
        raise AssertionError("_ChildHandle must remain a nominal private type")
    slots = next(
        node.value
        for node in handle.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__slots__" for target in node.targets)
    )
    assert isinstance(slots, ast.Tuple)
    assert {element.value for element in slots.elts if isinstance(element, ast.Constant)} == {
        "_abort",
        "_drive",
        "_fence",
        "_release",
    }
    assert {
        node.name
        for node in handle.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and not node.name.startswith("_")
    } == {"abort", "drive", "fence", "release"}


def test_public_graph_is_a_stateless_facade_over_the_authoritative_transition_path() -> None:
    graph = _top_level_definition("execution/facade.py", "Graph")
    if not isinstance(graph, ast.ClassDef):
        raise AssertionError("Graph must remain a class")
    slots = next(
        node.value
        for node in graph.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__slots__" for target in node.targets)
    )
    assert isinstance(slots, ast.Tuple)
    assert {element.value for element in slots.elts if isinstance(element, ast.Constant)} == {
        "_builder_state",
        "_compiled_owner",
        "_definition_id",
        "_version",
    }
    assert not {
        "_state",
        "_current_state",
        "_run_state",
        "_session",
        "_outputs",
    } & {node.id for node in ast.walk(graph) if isinstance(node, ast.Name)}
    assert _call_owner_modules("reduce_graph_run") == (
        "execution/claim.py",
        "execution/commit.py",
        "execution/engine/recovery.py",
        "execution/engine/session.py",
        "execution/invocation.py",
    )

    public_tree = _module("execution/__init__.py")
    public_exports = next(
        node.value
        for node in public_tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
    )
    assert isinstance(public_exports, ast.List)
    assert [element.value for element in public_exports.elts if isinstance(element, ast.Constant)] == ["Graph"]


def test_graph_facade_delegates_private_runtime_orchestration() -> None:
    facade = _module("execution/facade.py")
    facade_names = _defined_names(facade)

    assert (
        not {
            "_PlannedState",
            "PlannedFence",
            "PlannedResume",
            "project_graph_result",
            "validate_context",
        }
        & facade_names
    )
    assert _symbol_owners(
        frozenset(
            {
                "_PlannedState",
                "PlannedFence",
                "PlannedResume",
                "GraphTransition",
                "project_graph_result",
            }
        )
    ) == {
        "_PlannedState": ("execution/invocation.py",),
        "PlannedFence": ("execution/invocation.py",),
        "PlannedResume": ("execution/invocation.py",),
        "GraphTransition": ("execution/commit.py",),
        "project_graph_result": ("execution/family_driver.py",),
    }


def test_graph_transition_modules_do_not_alias_contracts() -> None:
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


def test_graph_run_lifecycle_has_one_running_and_three_terminal_states() -> None:
    status = _top_level_definition("state/graph_state/model.py", "GraphRunStatus")
    assert isinstance(status, ast.ClassDef)
    members = {
        target.id
        for statement in status.body
        if isinstance(statement, ast.Assign)
        for target in statement.targets
        if isinstance(target, ast.Name)
    }
    assert members == {"RUNNING", "COMPLETED", "FAILED", "ABORTED"}


def test_frontier_transition_plan_is_the_single_compiled_execution_lowering() -> None:
    assert _class_fields("execution/graph/topology.py", "FrontierTransitionPlan") == {
        "entries": "tuple[GraphNodeId, ...]",
        "direct_targets": "FrozenMap[GraphNodeId, tuple[GraphNodeId, ...]]",
        "conditional_targets": "FrozenMap[GraphNodeId, FrozenMap[GraphRouteId, GraphNodeId]]",
        "joins_by_source": "FrozenMap[GraphNodeId, tuple[CompiledJoin, ...]]",
        "materializations": "FrozenMap[GraphNodeId, MaterializationPlan[GraphValueT]]",
        "publications": "FrozenMap[GraphNodeId, FrameDescriptor[GraphValueT]]",
        "graph_outputs": "GraphOutputBindings[GraphValueT]",
        "resource_order": "tuple[ResourceId, ...]",
        "activation_gates": "FrozenMap[GraphNodeId, tuple[ActivationGate, ...]]",
    }
    compiled_graph = _top_level_definition("execution/graph/topology.py", "CompiledGraph")
    assert isinstance(compiled_graph, ast.ClassDef)
    assert _class_fields("execution/graph/topology.py", "CompiledGraph") == {
        "definition_id": "GraphDefinitionId",
        "version": "GraphDefinitionVersion",
        "definition_scope": "DefinitionScope",
        "nodes": "FrozenMap[GraphNodeId, GraphNode[GraphValueT]]",
        "nested_graphs": "FrozenMap[GraphNodeId, 'CompiledGraph[GraphValueT]']",
        "graph_input_descriptor": "FrameDescriptor[GraphValueT]",
        "graph_output_descriptor": "FrameDescriptor[GraphValueT]",
        "transition": "FrontierTransitionPlan[GraphValueT]",
        "resources": "FrozenMap[ResourceId, ResourceDefinition]",
        "resume_input": "ResumeInputBinding[GraphValueT] | None",
    }
    assert all(isinstance(statement, ast.AnnAssign) for statement in compiled_graph.body)


def test_recovery_consumes_shared_claim_and_settlement_lowering() -> None:
    recovery = _module("execution/engine/recovery.py")
    forbidden = {
        "ClaimGraphExecution",
        "SettleGraphNode",
        "SucceededGraphNodeOutcome",
        "FailedGraphNodeOutcome",
        "InterruptedGraphNodeOutcome",
        "admit_tasks",
        "initial_resource_snapshot",
    }
    names = {node.id for node in ast.walk(recovery) if isinstance(node, ast.Name)}

    assert not forbidden & names
    assert {"claim_resource_snapshot", "project_claim_command", "project_success_settlement"} <= names
