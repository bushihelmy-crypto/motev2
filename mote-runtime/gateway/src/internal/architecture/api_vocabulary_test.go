package architecture_test

import (
	"go/ast"
	"go/parser"
	"go/token"
	"io/fs"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
)

func TestAPIDoesNotReintroduceAmbiguousProviderVocabulary(t *testing.T) {
	apiRoot := filepath.Join(sourceRoot(t), "api")
	var violations []string
	err := filepath.WalkDir(apiRoot, func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".go") || strings.HasSuffix(entry.Name(), "_test.go") {
			return nil
		}

		file, parseErr := parser.ParseFile(token.NewFileSet(), path, nil, 0)
		if parseErr != nil {
			return parseErr
		}
		ast.Inspect(file, func(node ast.Node) bool {
			switch declaration := node.(type) {
			case *ast.TypeSpec:
				appendProviderIdentifierViolation(path, declaration.Name, &violations)
			case *ast.ValueSpec:
				for _, name := range declaration.Names {
					appendProviderIdentifierViolation(path, name, &violations)
				}
			case *ast.FuncDecl:
				appendProviderIdentifierViolation(path, declaration.Name, &violations)
			case *ast.Field:
				for _, name := range declaration.Names {
					appendProviderIdentifierViolation(path, name, &violations)
				}
				if declaration.Tag != nil {
					appendLegacyJSONTagViolation(path, declaration.Tag.Value, &violations)
				}
			}
			return true
		})
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}

	sort.Strings(violations)
	if len(violations) != 0 {
		t.Fatalf("ambiguous provider vocabulary found at the public API boundary: %v", violations)
	}
}

func appendProviderIdentifierViolation(path string, identifier *ast.Ident, violations *[]string) {
	if identifier != nil && ast.IsExported(identifier.Name) && strings.Contains(identifier.Name, "Provider") {
		*violations = append(*violations, path+":"+identifier.Name)
	}
}

func appendLegacyJSONTagViolation(path, quotedTag string, violations *[]string) {
	tag := strings.Trim(quotedTag, "`")
	name := strings.Split(reflect.StructTag(tag).Get("json"), ",")[0]
	if name == "provider" || strings.HasPrefix(name, "provider_") {
		*violations = append(*violations, path+":json:"+name)
	}
}
