package complexity_test

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"testing"
)

// The measured set is explicit. Test infrastructure and the process shell do
// not inflate the production architecture budget.
var measuredRoots = map[string]bool{
	"api": true, "internal/application": true, "internal/admission": true,
	"internal/plan": true, "internal/model": true, "internal/protocol": true,
	"internal/service": true, "internal/cache": true, "internal/usage": true,
	"internal/receipt": true, "internal/telemetry": true, "internal/upstream": true,
	"protocols": true, "connectors": true, "upstream": true, "ports": true,
}

type metrics struct {
	ProductionFiles      int `json:"production_files"`
	TopLevelDeclarations int `json:"top_level_declarations"`
	FunctionDefinitions  int `json:"function_definitions"`
	DecisionPoints       int `json:"decision_points"`
	MaxCyclomatic        int `json:"max_cyclomatic"`
	MaxNestingDepth      int `json:"max_nesting_depth"`
	ImportEdges          int `json:"import_edges"`
	PackageCount         int `json:"package_count"`
}

type baselineDocument struct {
	SchemaVersion int     `json:"schema_version"`
	Metrics       metrics `json:"metrics"`
}

func projectRoot(t *testing.T) string {
	t.Helper()
	_, source, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(source), "..", "..", ".."))
}

func isMeasured(relativeDir string) bool {
	for owner := range measuredRoots {
		if relativeDir == owner || strings.HasPrefix(relativeDir, owner+"/") {
			return true
		}
	}
	return false
}

func measure(t *testing.T) metrics {
	t.Helper()
	root := filepath.Join(projectRoot(t), "src")
	result := metrics{}
	packages := map[string]bool{}
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".go") || strings.HasSuffix(entry.Name(), "_test.go") {
			return nil
		}
		relativeDir, err := filepath.Rel(root, filepath.Dir(path))
		if err != nil {
			return err
		}
		relativeDir = filepath.ToSlash(relativeDir)
		if relativeDir == "." || !isMeasured(relativeDir) {
			return nil
		}
		file, err := parser.ParseFile(token.NewFileSet(), path, nil, 0)
		if err != nil {
			return err
		}
		result.ProductionFiles++
		packages[relativeDir] = true
		result.ImportEdges += len(file.Imports)
		for _, declaration := range file.Decls {
			result.TopLevelDeclarations++
			function, ok := declaration.(*ast.FuncDecl)
			if !ok || function.Body == nil {
				continue
			}
			result.FunctionDefinitions++
			decisions, nesting := functionMetrics(function.Body)
			result.DecisionPoints += decisions
			if decisions+1 > result.MaxCyclomatic {
				result.MaxCyclomatic = decisions + 1
			}
			if nesting > result.MaxNestingDepth {
				result.MaxNestingDepth = nesting
			}
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	result.PackageCount = len(packages)
	return result
}

type complexityVisitor struct {
	depth       int
	maxDepth    int
	decisions   int
	controlPath []bool
}

func (v *complexityVisitor) Visit(node ast.Node) ast.Visitor {
	if node == nil {
		if len(v.controlPath) == 0 {
			return v
		}
		if v.controlPath[len(v.controlPath)-1] {
			v.depth--
		}
		v.controlPath = v.controlPath[:len(v.controlPath)-1]
		return v
	}
	// A closure is a separate function boundary and is measured separately by
	// its own declaration when it is named; do not charge it to its parent.
	if _, isClosure := node.(*ast.FuncLit); isClosure {
		return nil
	}
	control := false
	switch value := node.(type) {
	case *ast.IfStmt, *ast.ForStmt, *ast.RangeStmt, *ast.SwitchStmt,
		*ast.TypeSwitchStmt, *ast.SelectStmt:
		control = true
		v.decisions++
	case *ast.CaseClause, *ast.CommClause:
		v.decisions++
	case *ast.BinaryExpr:
		if value.Op == token.LAND || value.Op == token.LOR {
			v.decisions++
		}
	}
	if control {
		v.depth++
		if v.depth > v.maxDepth {
			v.maxDepth = v.depth
		}
	}
	v.controlPath = append(v.controlPath, control)
	return v
}

func functionMetrics(body *ast.BlockStmt) (int, int) {
	visitor := &complexityVisitor{}
	ast.Walk(visitor, body)
	return visitor.decisions, visitor.maxDepth
}

func loadBaseline(t *testing.T) metrics {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(projectRoot(t), "quality", "complexity-baseline.json"))
	if err != nil {
		t.Fatal(err)
	}
	decoder := json.NewDecoder(strings.NewReader(string(data)))
	decoder.DisallowUnknownFields()
	var document baselineDocument
	if err := decoder.Decode(&document); err != nil {
		t.Fatal(err)
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		t.Fatalf("complexity baseline must contain one JSON document: %v", err)
	}
	if document.SchemaVersion != 1 {
		t.Fatalf("unsupported complexity baseline schema: %d", document.SchemaVersion)
	}
	return document.Metrics
}

func metricValues(value metrics) map[string]int {
	return map[string]int{
		"production_files":       value.ProductionFiles,
		"top_level_declarations": value.TopLevelDeclarations,
		"function_definitions":   value.FunctionDefinitions,
		"decision_points":        value.DecisionPoints,
		"max_cyclomatic":         value.MaxCyclomatic,
		"max_nesting_depth":      value.MaxNestingDepth,
		"import_edges":           value.ImportEdges,
		"package_count":          value.PackageCount,
	}
}

func TestComplexityRatchet(t *testing.T) {
	actual := metricValues(measure(t))
	baseline := metricValues(loadBaseline(t))
	var changed []string
	for name, value := range actual {
		if value != baseline[name] {
			changed = append(changed, fmt.Sprintf("%s=%d (baseline %d)", name, value, baseline[name]))
		}
	}
	sort.Strings(changed)
	if len(changed) != 0 {
		t.Fatalf("complexity ratchet changed: %s; update the baseline only after architecture review", strings.Join(changed, ", "))
	}
}
