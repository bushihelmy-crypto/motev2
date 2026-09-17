package architecture_test

import (
	"go/parser"
	"go/token"
	"io/fs"
	"path/filepath"
	"sort"
	"strings"
	"testing"
)

type importEdge struct {
	from string
	to   string
	file string
}

func productionEdges(t *testing.T) []importEdge {
	t.Helper()
	root := sourceRoot(t)
	var edges []importEdge
	err := filepath.Walk(root, func(path string, info fs.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() || !strings.HasSuffix(info.Name(), ".go") || strings.HasSuffix(info.Name(), "_test.go") {
			return nil
		}
		relative, relErr := filepath.Rel(root, filepath.Dir(path))
		if relErr != nil {
			return relErr
		}
		from := filepath.ToSlash(relative)
		if from == "." {
			from = "root"
		}
		tree, parseErr := parser.ParseFile(token.NewFileSet(), path, nil, 0)
		if parseErr != nil {
			return parseErr
		}
		for _, imported := range tree.Imports {
			name := strings.Trim(imported.Path.Value, `"`)
			if !strings.HasPrefix(name, modulePath+"/") {
				continue
			}
			to := strings.TrimPrefix(name, modulePath+"/")
			edges = append(edges, importEdge{from: from, to: to, file: path})
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return edges
}

func TestLayerDependenciesRemainOneWay(t *testing.T) {
	forbidden := map[string]map[string]bool{
		"api": {
			"internal": true, "protocols": true, "connectors": true, "upstream": true, "ports": true,
		},
		"internal/model": {
			"internal/protocol": true, "internal/service": true, "internal/application": true,
		},
		"internal/admission": {
			"internal/application": true, "internal/upstream": true,
			"protocols": true, "connectors": true, "upstream": true,
		},
		"internal/protocol": {
			"internal/application": true, "internal/admission": true, "internal/model": true,
			"internal/service": true, "internal/cache": true, "internal/usage": true,
			"internal/receipt": true, "internal/telemetry": true,
			"protocols": true, "connectors": true, "upstream": true,
		},
		"internal/service": {
			"internal/application": true, "internal/admission": true, "internal/model": true,
			"internal/protocol": true, "internal/cache": true, "internal/usage": true,
			"internal/receipt": true, "internal/telemetry": true,
			"protocols": true, "connectors": true, "upstream": true,
		},
		"internal/upstream": {
			"api": true, "internal/application": true, "internal/admission": true,
			"internal/model": true, "internal/protocol": true, "internal/service": true,
			"internal/cache": true, "internal/usage": true, "internal/receipt": true,
			"internal/telemetry": true, "ports": true,
			"protocols": true, "connectors": true, "upstream": true,
		},
		"internal/cache": {
			"internal/application": true, "protocols": true, "connectors": true, "upstream": true,
		},
		"internal/usage": {
			"internal/application": true, "protocols": true, "connectors": true,
		},
		"internal/receipt": {
			"internal/application": true, "protocols": true, "connectors": true,
		},
		"internal/telemetry": {
			"internal/application": true, "protocols": true, "connectors": true,
		},
		"ports": {
			"internal": true, "protocols": true, "connectors": true, "upstream": true,
		},
	}

	var violations []string
	for _, edge := range productionEdges(t) {
		for owner, blocked := range forbidden {
			// Concrete implementations are checked by the owner-aware rule
			// below.  They must be able to import their own contract package;
			// treating the whole owner tree as one forbidden blob would make
			// protocol/* and service/* unable to implement their interfaces.
			if isConcreteImplementation(edge.from, owner) {
				continue
			}
			if !strings.HasPrefix(edge.from, owner) || (len(edge.from) > len(owner) && edge.from[len(owner)] != '/') {
				continue
			}
			for target := range blocked {
				if edge.to == target || strings.HasPrefix(edge.to, target+"/") {
					violations = append(violations, edge.from+" -> "+edge.to+" ("+edge.file+")")
				}
			}
		}
	}
	sort.Strings(violations)
	if len(violations) != 0 {
		t.Fatalf("forbidden owner dependency edges: %v", violations)
	}
}

func TestConcretePackagesDoNotReachAcrossRuntimeOwners(t *testing.T) {
	var violations []string
	for _, edge := range productionEdges(t) {
		if concreteOwnerViolation(edge.from, edge.to) {
			violations = append(violations, edge.from+" -> "+edge.to+" ("+edge.file+")")
		}
	}
	sort.Strings(violations)
	if len(violations) != 0 {
		t.Fatalf("concrete owner dependency edges: %v", violations)
	}
}

// concreteOwnerViolation describes the actual implementation rule rather than
// treating an owner package and all of its children as one forbidden blob. A
// protocol implementation may use api, its own contract tree, and any outbound
// transport implementation. A service implementation may additionally use the
// external ports it needs for credential resolution. Upstream implementations
// stay transport-only. None of them may reach admission, model, application,
// or another runtime owner's implementation.
func concreteOwnerViolation(from, to string) bool {
	if isConcretePackage(from, "internal/protocol") {
		return !sameOwner(to, "api") &&
			!sameOwner(to, "internal/protocol") &&
			!sameOwner(to, "internal/upstream")
	}
	if isConcretePackage(from, "internal/service") {
		return !sameOwner(to, "api") &&
			!sameOwner(to, "ports") &&
			!sameOwner(to, "internal/service") &&
			!sameOwner(to, "internal/upstream")
	}
	if isConcretePackage(from, "internal/upstream") {
		return !sameOwner(to, "internal/upstream")
	}
	return false
}

func TestConcreteOwnerRuleKeepsSiblingContractsUsable(t *testing.T) {
	cases := []struct {
		from string
		to   string
		want bool
	}{
		{from: "internal/protocol/openai/chatcompletions", to: "internal/protocol", want: false},
		{from: "internal/protocol/openai/chatcompletions", to: "internal/protocol/stream", want: false},
		{from: "internal/protocol/openai/chatcompletions", to: "internal/upstream/httpclient", want: false},
		{from: "internal/protocol/openai/chatcompletions", to: "api", want: false},
		{from: "internal/protocol/openai/chatcompletions", to: "internal/service", want: true},
		{from: "internal/service/azure", to: "internal/service", want: false},
		{from: "internal/service/azure", to: "internal/service/config", want: false},
		{from: "internal/service/azure", to: "internal/upstream/httpclient", want: false},
		{from: "internal/service/azure", to: "ports", want: false},
		{from: "internal/service/azure", to: "internal/protocol/openai", want: true},
		{from: "internal/upstream/httpclient", to: "internal/upstream", want: false},
		{from: "internal/upstream/httpclient", to: "api", want: true},
	}
	for _, testCase := range cases {
		t.Run(testCase.from+"_to_"+testCase.to, func(t *testing.T) {
			if got := concreteOwnerViolation(testCase.from, testCase.to); got != testCase.want {
				t.Fatalf("concreteOwnerViolation(%q, %q) = %v, want %v", testCase.from, testCase.to, got, testCase.want)
			}
		})
	}
}

func isConcretePackage(path, owner string) bool {
	return strings.HasPrefix(path, owner+"/")
}

func isConcreteImplementation(path, owner string) bool {
	if owner != "internal/protocol" && owner != "internal/service" && owner != "internal/upstream" {
		return false
	}
	return isConcretePackage(path, owner)
}

func sameOwner(path, owner string) bool {
	return path == owner || (strings.HasPrefix(path, owner) && len(path) > len(owner) && path[len(owner)] == '/')
}

func TestNoProductionImportCycle(t *testing.T) {
	edges := productionEdges(t)
	graph := make(map[string][]string)
	for _, edge := range edges {
		graph[edge.from] = append(graph[edge.from], edge.to)
	}
	const (
		active = 1
		done   = 2
	)
	state := make(map[string]int)
	var stack []string
	var visit func(string) []string
	visit = func(node string) []string {
		switch state[node] {
		case active:
			for index, item := range stack {
				if item == node {
					return append(append([]string{}, stack[index:]...), node)
				}
			}
			return []string{node}
		case done:
			return nil
		}
		state[node] = active
		stack = append(stack, node)
		for _, next := range graph[node] {
			if cycle := visit(next); len(cycle) != 0 {
				return cycle
			}
		}
		stack = stack[:len(stack)-1]
		state[node] = done
		return nil
	}
	var nodes []string
	for node := range graph {
		nodes = append(nodes, node)
	}
	sort.Strings(nodes)
	for _, node := range nodes {
		if cycle := visit(node); len(cycle) != 0 {
			t.Fatalf("production import cycle: %s", strings.Join(cycle, " -> "))
		}
	}
}

func TestProductionDoesNotImportTestkit(t *testing.T) {
	var violations []string
	for _, edge := range productionEdges(t) {
		if edge.to == "internal/testkit" || strings.HasPrefix(edge.to, "internal/testkit/") {
			violations = append(violations, edge.from+" -> "+edge.to+" ("+edge.file+")")
		}
	}
	sort.Strings(violations)
	if len(violations) != 0 {
		t.Fatalf("production dependencies on testkit: %v", violations)
	}
}
