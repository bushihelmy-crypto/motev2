package api

import (
	"strings"
	"testing"
)

func TestBaseModelVocabularyMatchesConformance(t *testing.T) {
	tests := []struct {
		name  string
		value string
		valid bool
	}{
		{name: "empty", valid: false},
		{name: "leading whitespace", value: " model", valid: false},
		{name: "trailing whitespace", value: "model ", valid: false},
		{name: "qualified name", value: "provider/model", valid: false},
		{name: "unsupported punctuation", value: "model!", valid: false},
		{name: "leading punctuation", value: "-model", valid: false},
		{name: "single byte", value: "m", valid: true},
		{name: "every allowed punctuation", value: "m_A.Z:9-0", valid: true},
		{name: "maximum length", value: "m" + strings.Repeat("a", 255), valid: true},
		{name: "too long", value: strings.Repeat("m", 257), valid: false},
		{name: "non ascii", value: "模型", valid: false},
		{name: "bare name with colon", value: "model:0", valid: true},
		{name: "bare name", value: "model-v1.2", valid: true},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			err := ValidateBaseModel(test.value)
			if test.valid && err != nil {
				t.Fatalf("ValidateBaseModel(%q) rejected a valid bare name: %v", test.value, err)
			}
			if !test.valid && err == nil {
				t.Fatalf("ValidateBaseModel(%q) accepted an invalid identity", test.value)
			}
		})
	}
}

func TestRuntimeIdentityVocabularyMatchesConformance(t *testing.T) {
	for _, value := range []string{
		"a",
		"fixture",
		"service.kind-1",
		"a_b",
		"a" + strings.Repeat("z", 127),
	} {
		if err := ValidateServiceKind(value); err != nil {
			t.Fatalf("valid service kind %q rejected: %v", value, err)
		}
	}
	for _, value := range []string{
		"a.b",
		"openai.responses.v1",
		"fixture.video.v1",
		"a_1.b-2",
		"a." + strings.Repeat("z", 126),
	} {
		if err := ValidateProtocolID(value); err != nil {
			t.Fatalf("valid protocol id %q rejected: %v", value, err)
		}
	}
	for _, value := range []string{"", "Service", "service/bad", "service:bad", "服务", strings.Repeat("a", 129)} {
		if err := ValidateServiceKind(value); err == nil {
			t.Fatalf("invalid service kind %q accepted", value)
		}
	}
	for _, value := range []string{
		"",
		"Protocol.Bad",
		"protocol",
		"protocol/bad",
		"protocol:bad",
		"protocol.",
		"protocol..bad",
		"协议.v1",
		"a." + strings.Repeat("z", 127),
	} {
		if err := ValidateProtocolID(value); err == nil {
			t.Fatalf("invalid protocol id %q accepted", value)
		}
	}
}

func TestArtifactKindHasOneCanonicalModality(t *testing.T) {
	want := map[string]Modality{
		ArtifactKindText:  ModalityText,
		ArtifactKindImage: ModalityImage,
		ArtifactKindAudio: ModalityAudio,
		ArtifactKindMusic: ModalityMusic,
		ArtifactKindVideo: ModalityVideo,
	}
	for kind, modality := range want {
		if got, ok := ArtifactModality(kind); !ok || got != modality {
			t.Fatalf("artifact kind %q resolved to %q, %v; want %q, true", kind, got, ok, modality)
		}
	}
	if _, ok := ArtifactModality("provider-specific"); ok {
		t.Fatal("unknown artifact kind acquired an implicit modality")
	}
}
