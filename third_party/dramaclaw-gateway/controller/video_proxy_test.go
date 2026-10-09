package controller

import "testing"

func TestSameURLOrigin(t *testing.T) {
	tests := []struct {
		name    string
		result  string
		baseURL string
		want    bool
	}{
		{name: "same private origin", result: "http://192.168.2.222:8188/view?filename=out.mp4", baseURL: "http://192.168.2.222:8188", want: true},
		{name: "different port", result: "http://192.168.2.222:8190/view?filename=out.mp4", baseURL: "http://192.168.2.222:8188", want: false},
		{name: "different host", result: "http://example.com/view?filename=out.mp4", baseURL: "http://192.168.2.222:8188", want: false},
		{name: "relative URL", result: "/view?filename=out.mp4", baseURL: "http://192.168.2.222:8188", want: false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := sameURLOrigin(tt.result, tt.baseURL); got != tt.want {
				t.Fatalf("sameURLOrigin() = %v, want %v", got, tt.want)
			}
		})
	}
}
