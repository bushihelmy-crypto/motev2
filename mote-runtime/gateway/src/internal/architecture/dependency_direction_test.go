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
		"internal/application": {
			"protocols": true, "connectors": true, "upstream": true,
		},
		"internal/model": {
			"internal/protocol": true, "internal/service": true, "internal/application": true,
		},
		"internal/protocol": {
			"internal/application": true, "internal/service": true, "protocols": true, "connectors": true,
		},
		"internal/service": {
			"internal/application": true, "protocols": true, "connectors": true,
		},
		"internal/admission": {
			"protocols": true, "connectors": true, "upstream": true,
		},
		"internal/plan": {
			"internal/application": true, "protocols": true, "connectors": true,
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
	forbidden := map[string]map[string]bool{
		"protocols": {
			"internal/application": true, "internal/admission": true, "internal/model": true,
			"internal/plan": true, "internal/cache": true, "internal/usage": true,
			"internal/receipt": true, "internal/service": true, "internal/telemetry": true,
			"connectors": true, "upstream": true, "protocols": true,
		},
		"connectors": {
			"internal/application": true, "internal/admission": true, "internal/model": true,
			"internal/plan": true, "internal/cache": true, "internal/usage": true,
			"internal/receipt": true, "internal/protocol": true, "internal/telemetry": true,
			"protocols": true, "connectors": true, "upstream": true,
		},
		"upstream": {
			"api": true, "internal/application": true, "internal/admission": true,
			"internal/model": true, "internal/plan": true, "internal/cache": true,
			"internal/usage": true, "internal/receipt": true, "internal/protocol": true,
			"internal/service": true, "internal/telemetry": true, "protocols": true,
			"connectors": true, "upstream": true,
		},
	}

	var violations []string
	for _, edge := range productionEdges(t) {
		for owner, blocked := range forbidden {
			if !sameOwner(edge.from, owner) {
				continue
			}
			for target := range blocked {
				if sameOwner(edge.to, target) {
					violations = append(violations, edge.from+" -> "+edge.to+" ("+edge.file+")")
				}
			}
		}
	}
	sort.Strings(violations)
	if len(violations) != 0 {
		t.Fatalf("concrete owner dependency edges: %v", violations)
	}
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
