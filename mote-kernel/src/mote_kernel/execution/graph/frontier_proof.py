"""Symbolic terminal-frontier proof owned by the graph compiler.

The runtime advances one settled frontier at a time.  This module models the
same transition relation over Boolean state bits and computes its reachable
fixed point symbolically.  A frontier is never expanded into a Cartesian
product of independent route choices; the BDD keeps those choices factored
until a concrete coexistence question is asked.
"""

from dataclasses import dataclass
from enum import Enum, auto
from typing import TypeAlias

from mote_kernel.execution.errors import GraphValidationError
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.topology import CompiledJoin
from mote_kernel.state.graph_state import GraphNodeId, GraphRouteId

_Route: TypeAlias = GraphRouteId | None
_DuplicateCause: TypeAlias = tuple[int, GraphNodeId, tuple[GraphNodeId, ...]]


class _BddOperation(Enum):
    AND = auto()
    OR = auto()
    XOR = auto()


@dataclass(frozen=True, slots=True)
class _BddNode:
    variable: int
    low: int
    high: int


class _Bdd:
    """Small reduced ordered BDD manager for one compiler proof."""

    def __init__(self) -> None:
        self._nodes: list[_BddNode | None] = [None, None]
        self._unique: dict[tuple[int, int, int], int] = {}
        self._apply_cache: dict[tuple[_BddOperation, int, int], int] = {}
        self._not_cache: dict[int, int] = {}
        self._exists_cache: dict[tuple[int, frozenset[int]], int] = {}
        self._rename_cache: dict[tuple[int, tuple[tuple[int, int], ...]], int] = {}
        self._next_variable = 0

    @property
    def false(self) -> int:
        return 0

    @property
    def true(self) -> int:
        return 1

    def variable(self) -> int:
        variable = self._next_variable
        self._next_variable += 1
        return self._mk(variable, self.false, self.true)

    def variable_index(self, reference: int) -> int:
        node = self._node(reference)
        assert node is not None, "BDD variable reference must be a non-terminal node"
        return node.variable

    def _node(self, reference: int) -> _BddNode | None:
        return self._nodes[reference]

    def _mk(self, variable: int, low: int, high: int) -> int:
        if low == high:
            return low
        key = (variable, low, high)
        existing = self._unique.get(key)
        if existing is not None:
            return existing
        reference = len(self._nodes)
        self._nodes.append(_BddNode(variable, low, high))
        self._unique[key] = reference
        return reference

    def _cofactor(self, reference: int, variable: int) -> tuple[int, int]:
        node = self._node(reference)
        if node is None or node.variable != variable:
            return reference, reference
        return node.low, node.high

    def _apply_terminal(self, operation: _BddOperation, first: int, second: int) -> int | None:
        if operation is _BddOperation.AND:
            if first == self.false or second == self.false:
                return self.false
            if first == self.true:
                return second
            if second == self.true or first == second:
                return first
        elif operation is _BddOperation.OR:
            if first == self.true or second == self.true:
                return self.true
            if first == self.false:
                return second
            if second == self.false or first == second:
                return first
        else:
            if first == self.false:
                return second
            if first == second:
                return self.false
        return None

    def apply(self, operation: _BddOperation, first: int, second: int) -> int:
        if first > second:
            first, second = second, first
        key = (operation, first, second)
        cached = self._apply_cache.get(key)
        if cached is not None:
            return cached
        pending: list[tuple[int, int, bool]] = [(first, second, False)]
        while pending:
            current_first, current_second, expanded = pending.pop()
            if current_first > current_second:
                current_first, current_second = current_second, current_first
            current_key = (operation, current_first, current_second)
            if current_key in self._apply_cache:
                continue
            terminal = self._apply_terminal(operation, current_first, current_second)
            if terminal is not None:
                self._apply_cache[current_key] = terminal
                continue
            first_node = self._node(current_first)
            second_node = self._node(current_second)
            variables = tuple(node.variable for node in (first_node, second_node) if node is not None)
            variable = min(variables)
            if not expanded:
                first_low, first_high = self._cofactor(current_first, variable)
                second_low, second_high = self._cofactor(current_second, variable)
                pending.append((current_first, current_second, True))
                pending.append((first_high, second_high, False))
                pending.append((first_low, second_low, False))
                continue
            first_low, first_high = self._cofactor(current_first, variable)
            second_low, second_high = self._cofactor(current_second, variable)
            low_first, low_second = sorted((first_low, second_low))
            high_first, high_second = sorted((first_high, second_high))
            low_key = (operation, low_first, low_second)
            high_key = (operation, high_first, high_second)
            self._apply_cache[current_key] = self._mk(
                variable,
                self._apply_cache[low_key],
                self._apply_cache[high_key],
            )
        return self._apply_cache[key]

    def conjunction(self, values: tuple[int, ...]) -> int:
        pending = list(values)
        if not pending:
            return self.true
        while len(pending) > 1:
            pending = [
                self.apply(_BddOperation.AND, pending[position], pending[position + 1])
                for position in range(0, len(pending) - 1, 2)
            ] + (pending[-1:] if len(pending) % 2 else [])
        return pending[0]

    def disjunction(self, values: tuple[int, ...]) -> int:
        pending = list(values)
        if not pending:
            return self.false
        while len(pending) > 1:
            pending = [
                self.apply(_BddOperation.OR, pending[position], pending[position + 1])
                for position in range(0, len(pending) - 1, 2)
            ] + (pending[-1:] if len(pending) % 2 else [])
        return pending[0]

    def negate(self, reference: int) -> int:
        cached = self._not_cache.get(reference)
        if cached is not None:
            return cached
        values = {self.false: self.true, self.true: self.false}
        pending: list[tuple[int, bool]] = [(reference, False)]
        while pending:
            current, expanded = pending.pop()
            if current in values:
                continue
            cached = self._not_cache.get(current)
            if cached is not None:
                values[current] = cached
                continue
            node = self._node(current)
            assert node is not None
            if not expanded:
                pending.append((current, True))
                pending.append((node.high, False))
                pending.append((node.low, False))
                continue
            result = self._mk(node.variable, values[node.low], values[node.high])
            self._not_cache[current] = result
            values[current] = result
        return values[reference]

    def equivalence(self, first: int, second: int) -> int:
        return self.negate(self.apply(_BddOperation.XOR, first, second))

    def exists(self, reference: int, variables: frozenset[int]) -> int:
        if reference < 2 or not variables:
            return reference
        key = (reference, variables)
        cached = self._exists_cache.get(key)
        if cached is not None:
            return cached
        values: dict[int, int] = {self.false: self.false, self.true: self.true}
        pending: list[tuple[int, bool]] = [(reference, False)]
        while pending:
            current, expanded = pending.pop()
            if current in values:
                continue
            current_key = (current, variables)
            cached = self._exists_cache.get(current_key)
            if cached is not None:
                values[current] = cached
                continue
            node = self._node(current)
            assert node is not None
            if not expanded:
                pending.append((current, True))
                pending.append((node.high, False))
                pending.append((node.low, False))
                continue
            low = values[node.low]
            high = values[node.high]
            result = (
                self.apply(_BddOperation.OR, low, high)
                if node.variable in variables
                else self._mk(node.variable, low, high)
            )
            self._exists_cache[current_key] = result
            values[current] = result
        return values[reference]

    def rename(self, reference: int, mapping: dict[int, int]) -> int:
        if reference < 2 or not mapping:
            return reference
        frozen_mapping = tuple(sorted(mapping.items()))
        key = (reference, frozen_mapping)
        cached = self._rename_cache.get(key)
        if cached is not None:
            return cached
        values: dict[int, int] = {self.false: self.false, self.true: self.true}
        pending: list[tuple[int, bool]] = [(reference, False)]
        while pending:
            current, expanded = pending.pop()
            if current in values:
                continue
            current_key = (current, frozen_mapping)
            cached = self._rename_cache.get(current_key)
            if cached is not None:
                values[current] = cached
                continue
            node = self._node(current)
            assert node is not None
            if not expanded:
                pending.append((current, True))
                pending.append((node.high, False))
                pending.append((node.low, False))
                continue
            variable = mapping.get(node.variable, node.variable)
            # ITE re-establishes the manager's variable ordering even when a
            # next-state variable is renamed into an earlier current-state slot.
            result = self.ite(self._mk(variable, self.false, self.true), values[node.high], values[node.low])
            self._rename_cache[current_key] = result
            values[current] = result
        return values[reference]

    def ite(self, condition: int, when_true: int, when_false: int) -> int:
        return self.apply(
            _BddOperation.OR,
            self.apply(_BddOperation.AND, condition, when_true),
            self.apply(_BddOperation.AND, self.negate(condition), when_false),
        )

    def satisfiable(self, reference: int) -> bool:
        return reference != self.false


@dataclass(frozen=True, slots=True)
class _StateVariables:
    current: int
    following: int


@dataclass(frozen=True, slots=True)
class _Choice:
    route: _Route
    variable: int


class _FrontierRelation:
    """Lower one compiled control topology to a symbolic transition relation."""

    def __init__(
        self,
        entries: tuple[GraphNodeId, ...],
        route_options: dict[GraphNodeId, tuple[_Route, ...]],
        direct_targets: dict[GraphNodeId, set[GraphNodeId]],
        conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
        joins_by_source: dict[GraphNodeId, list[CompiledJoin]],
    ) -> None:
        self.bdd = _Bdd()
        self.entries = entries
        self.nodes = self._symbolic_order(route_options, direct_targets, conditional_targets, joins_by_source)
        self.route_options = route_options
        self.direct_targets = direct_targets
        self.conditional_targets = conditional_targets
        self.joins = self._unique_joins(joins_by_source)
        node_current: dict[GraphNodeId, int] = {}
        node_following: dict[GraphNodeId, int] = {}
        self.choices: dict[GraphNodeId, tuple[_Choice, ...]] = {}
        for node in self.nodes:
            node_current[node] = self.bdd.variable()
            self.choices[node] = self._choices_for_node(node)
            node_following[node] = self.bdd.variable()
        self.choices = {node: choices for node, choices in self.choices.items() if choices}
        join_keys = self._join_state_keys()
        join_current = {key: self.bdd.variable() for key in join_keys}
        join_following = {key: self.bdd.variable() for key in join_keys}
        self.node_state = {node: _StateVariables(node_current[node], node_following[node]) for node in self.nodes}
        self.join_state = {key: _StateVariables(join_current[key], join_following[key]) for key in join_keys}
        self.current_variables = tuple(variables.current for variables in self.node_state.values()) + tuple(
            variable.current for variable in self.join_state.values()
        )
        self.following_variables = tuple(variables.following for variables in self.node_state.values()) + tuple(
            variable.following for variable in self.join_state.values()
        )
        self.choice_variables = tuple(choice.variable for choices in self.choices.values() for choice in choices)
        self.duplicate_causes: list[_DuplicateCause] = []
        self.choice_constraints = self._choice_constraints()
        self.relation = self._build_relation()
        self.initial = self._initial_state()
        self.following_empty = self.bdd.conjunction(
            tuple(self.bdd.negate(variable) for variable in self.following_variables)
        )

    @staticmethod
    def _unique_joins(joins_by_source: dict[GraphNodeId, list[CompiledJoin]]) -> tuple[CompiledJoin, ...]:
        return tuple(
            sorted(
                {join for joins in joins_by_source.values() for join in joins},
                key=lambda join: (join.identity.sources, join.identity.target),
            )
        )

    @staticmethod
    def _symbolic_order(
        route_options: dict[GraphNodeId, tuple[_Route, ...]],
        direct_targets: dict[GraphNodeId, set[GraphNodeId]],
        conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
        joins_by_source: dict[GraphNodeId, list[CompiledJoin]],
    ) -> tuple[GraphNodeId, ...]:
        """Group independent control components before allocating BDD variables."""

        neighbours: dict[GraphNodeId, set[GraphNodeId]] = {node: set() for node in route_options}
        for source, targets in direct_targets.items():
            for target in targets:
                neighbours[source].add(target)
                neighbours[target].add(source)
        for source, targets in conditional_targets.items():
            for target in targets.values():
                if target != END:
                    neighbours[source].add(target)
                    neighbours[target].add(source)
        for joins in joins_by_source.values():
            for join in joins:
                members = join.identity.sources + ((join.identity.target,) if join.identity.target != END else ())
                for first in members:
                    neighbours[first].update(member for member in members if member != first)
        ordered: list[GraphNodeId] = []
        unseen = set(route_options)
        while unseen:
            root = min(unseen)
            component = {root}
            pending = [root]
            unseen.remove(root)
            while pending:
                source = pending.pop()
                for target in sorted(neighbours[source]):
                    if target in unseen:
                        unseen.remove(target)
                        component.add(target)
                        pending.append(target)
            ordered.extend(sorted(component))
        return tuple(ordered)

    def _join_state_keys(self) -> tuple[tuple[CompiledJoin, int, GraphNodeId], ...]:
        keys: list[tuple[CompiledJoin, int, GraphNodeId]] = []
        for join in self.joins:
            maximum = max(offset for _source, offset in join.source_target_offsets)
            for remaining in range(1, maximum + 1):
                keys.extend((join, remaining, source) for source in join.identity.sources)
        return tuple(keys)

    def _choices_for_node(self, node: GraphNodeId) -> tuple[_Choice, ...]:
        conditional = self.conditional_targets[node]
        options = tuple(sorted(conditional)) if conditional else self.route_options[node]
        return tuple(_Choice(route, self.bdd.variable()) for route in options) if len(options) > 1 else ()

    def _state_literal(self, variable: int, value: bool) -> int:
        return variable if value else self.bdd.negate(variable)

    def _choice_literal(self, node: GraphNodeId, route: _Route) -> int:
        choices = self.choices.get(node, ())
        if choices:
            for choice in choices:
                if choice.route == route:
                    return choice.variable
            return self.bdd.false
        options = self.conditional_targets[node]
        if options:
            return self.bdd.true if route in options else self.bdd.false
        return self.bdd.true if route in self.route_options[node] else self.bdd.false

    def _exactly_one_when_active(self, node: GraphNodeId, active: int) -> int:
        choices = self.choices[node]
        literals = tuple(choice.variable for choice in choices)
        at_least_one = self.bdd.disjunction(literals)
        clauses = [self.bdd.ite(active, at_least_one, self.bdd.true)]
        for position, first in enumerate(literals):
            clauses.extend(
                self.bdd.ite(
                    active,
                    self.bdd.negate(self.bdd.apply(_BddOperation.AND, first, second)),
                    self.bdd.true,
                )
                for second in literals[position + 1 :]
            )
        return self.bdd.conjunction(tuple(clauses))

    def _choice_constraints(self) -> int:
        return self.bdd.conjunction(
            tuple(self._exactly_one_when_active(node, self.node_state[node].current) for node in self.choices)
        )

    def _join_arrival(self, join: CompiledJoin, remaining: int, source: GraphNodeId) -> int:
        pending = self.join_state[(join, remaining, source)].current
        arrivals = [pending]
        if join.target_offset(source) == remaining:
            arrivals.append(self.node_state[source].current)
        return self.bdd.disjunction(tuple(arrivals))

    def _build_relation(self) -> int:
        constraints: list[int] = []
        constraints.append(self.choice_constraints)

        causes: dict[GraphNodeId, list[tuple[int, tuple[GraphNodeId, ...]]]] = {node: [] for node in self.nodes}
        for source in self.nodes:
            active = self.node_state[source].current
            for target in self.direct_targets[source]:
                causes[target].append((active, (source,)))
            for route, target in self.conditional_targets[source].items():
                if target != END:
                    causes[target].append(
                        (self.bdd.apply(_BddOperation.AND, active, self._choice_literal(source, route)), (source,))
                    )

        for join in self.joins:
            maximum = max(offset for _source, offset in join.source_target_offsets)
            for remaining in range(1, maximum + 1):
                arrivals = {source: self._join_arrival(join, remaining, source) for source in join.identity.sources}
                any_arrival = self.bdd.disjunction(tuple(arrivals.values()))
                complete = self.bdd.conjunction(tuple(arrivals.values()))
                if remaining == 1:
                    constraints.append(self.bdd.ite(any_arrival, complete, self.bdd.true))
                    if join.identity.target != END:
                        causes[join.identity.target].append((complete, join.identity.sources))
                else:
                    constraints.append(self.bdd.negate(complete))
                for source in join.identity.sources:
                    offset = join.target_offset(source)
                    if offset == remaining:
                        pending = self.join_state[(join, remaining, source)].current
                        duplicate = self.bdd.apply(_BddOperation.AND, pending, self.node_state[source].current)
                        constraints.append(self.bdd.negate(duplicate))
                    if remaining > 1:
                        next_variable = self.join_state[(join, remaining - 1, source)].following
                        constraints.append(self.bdd.equivalence(next_variable, arrivals[source]))

        for target in self.nodes:
            target_causes = causes[target]
            for position, (first, first_sources) in enumerate(target_causes):
                for second, second_sources in target_causes[position + 1 :]:
                    duplicate = self.bdd.apply(_BddOperation.AND, first, second)
                    self.duplicate_causes.append((duplicate, target, tuple(sorted({*first_sources, *second_sources}))))
                    constraints.append(self.bdd.negate(duplicate))
            expression = self.bdd.disjunction(tuple(cause for cause, _sources in target_causes))
            constraints.append(self.bdd.equivalence(self.node_state[target].following, expression))
        for join in self.joins:
            maximum = max(offset for _source, offset in join.source_target_offsets)
            constraints.extend(
                self.bdd.negate(self.join_state[(join, maximum, source)].following) for source in join.identity.sources
            )
        return self.bdd.conjunction(tuple(constraints))

    def _initial_state(self) -> int:
        entries = frozenset(self.entries)
        literals = tuple(self._state_literal(self.node_state[node].current, node in entries) for node in self.nodes)
        literals += tuple(self.bdd.negate(variable.current) for variable in self.join_state.values())
        return self.bdd.conjunction(literals)

    def reachable(self) -> int:
        reached = self.initial
        hidden = frozenset(
            self.bdd.variable_index(variable) for variable in (*self.current_variables, *self.choice_variables)
        )
        rename = {
            self.bdd.variable_index(following): self.bdd.variable_index(current)
            for following, current in zip(self.following_variables, self.current_variables, strict=True)
        }
        while True:
            image = self.bdd.rename(
                self.bdd.exists(self.bdd.apply(_BddOperation.AND, reached, self.relation), hidden),
                rename,
            )
            expanded = self.bdd.apply(_BddOperation.OR, reached, image)
            if expanded == reached:
                return reached
            reached = expanded

    def _terminal_route_presence(self, route: _Route) -> int:
        presence: list[int] = []
        for node in self.nodes:
            active = self.node_state[node].current
            conditional = self.conditional_targets[node]
            if conditional or route in self.route_options[node]:
                presence.append(self.bdd.apply(_BddOperation.AND, active, self._choice_literal(node, route)))
        return self.bdd.disjunction(tuple(presence))

    def completion_routes(self) -> frozenset[_Route]:
        reachable = self.reachable()
        for duplicate, target, sources in self.duplicate_causes:
            if self.bdd.satisfiable(self.bdd.conjunction((reachable, self.choice_constraints, duplicate))):
                if len(sources) > 1:
                    guidance = f"concurrent sources may be {sources!r}, declare graph.add_join({sources!r}, {target!r})"
                else:
                    guidance = f"source {next(iter(sources))!r} contributes more than one path to the same target"
                raise GraphValidationError(
                    f"target {target!r} has multiple activation gates without an explicit Join; {guidance}"
                )
        active = self.bdd.disjunction(tuple(variable.current for variable in self.node_state.values()))
        terminal_transition = self.bdd.conjunction((reachable, active, self.relation, self.following_empty))
        routes = tuple(
            sorted(
                {route for options in self.route_options.values() for route in options},
                key=lambda route: (route is not None, route or ""),
            )
        )
        route_presence = {route: self._terminal_route_presence(route) for route in routes}
        exposed: set[_Route] = set()
        for route in routes:
            if self.bdd.satisfiable(self.bdd.apply(_BddOperation.AND, terminal_transition, route_presence[route])):
                exposed.add(route)
        for position, first in enumerate(routes):
            first_presence = route_presence[first]
            for second in routes[position + 1 :]:
                conflicting = self.bdd.conjunction((terminal_transition, first_presence, route_presence[second]))
                if self.bdd.satisfiable(conflicting):
                    raise GraphValidationError("terminal frontier may expose conflicting completion routes")
        if not exposed:
            raise GraphValidationError("graph has no statically viable successful completion")
        return frozenset(exposed)


def prove_completion_routes(
    entries: tuple[GraphNodeId, ...],
    route_options: dict[GraphNodeId, tuple[_Route, ...]],
    direct_targets: dict[GraphNodeId, set[GraphNodeId]],
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
    joins_by_source: dict[GraphNodeId, list[CompiledJoin]],
) -> frozenset[_Route]:
    """Return the exact successful completion domain for one compiled graph."""

    return _FrontierRelation(
        entries,
        route_options,
        direct_targets,
        conditional_targets,
        joins_by_source,
    ).completion_routes()


__all__ = ["prove_completion_routes"]
