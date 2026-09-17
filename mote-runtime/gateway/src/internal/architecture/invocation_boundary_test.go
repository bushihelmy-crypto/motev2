package architecture_test

import (
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestPublicInvocationBoundaryHasNoRawRequestEntry(t *testing.T) {
	entries, err := os.ReadDir(sourceRoot(t))
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".go") || strings.HasSuffix(entry.Name(), "_test.go") {
			continue
		}
		path := filepath.Join(sourceRoot(t), entry.Name())
		file, err := parser.ParseFile(token.NewFileSet(), path, nil, 0)
		if err != nil {
			t.Fatal(err)
		}
		ast.Inspect(file, func(node ast.Node) bool {
			callable, ok := node.(*ast.FuncType)
			if !ok || callable.Params == nil {
				return true
			}
			for _, field := range callable.Params.List {
				if rawRequestType(field.Type) {
					t.Errorf("public Gateway callable in %s accepts raw api request DTO", entry.Name())
				}
			}
			return false
		})
	}
}

func rawRequestType(expression ast.Expr) bool {
	selector, ok := expression.(*ast.SelectorExpr)
	if !ok || (selector.Sel.Name != "LLMRequest" && selector.Sel.Name != "MediaRequest") {
		return false
	}
	owner, ok := selector.X.(*ast.Ident)
	return ok && owner.Name == "api"
}
