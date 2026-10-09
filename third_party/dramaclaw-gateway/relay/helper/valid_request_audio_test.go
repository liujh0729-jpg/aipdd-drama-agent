package helper

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	relayconstant "github.com/QuantumNous/new-api/relay/constant"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestGetAndValidAudioRequestPreservesOpenAISpeechFormats(t *testing.T) {
	gin.SetMode(gin.TestMode)
	for _, format := range []string{"wav", "aac", "flac"} {
		t.Run(format, func(t *testing.T) {
			c, _ := gin.CreateTestContext(httptest.NewRecorder())
			c.Request = httptest.NewRequest(
				http.MethodPost,
				"/v1/audio/speech",
				strings.NewReader(`{"model":"openai-compatible-tts","input":"hello","response_format":"`+format+`"}`),
			)
			c.Request.Header.Set("Content-Type", "application/json")

			request, err := GetAndValidAudioRequest(c, relayconstant.RelayModeAudioSpeech)

			require.NoError(t, err)
			assert.Equal(t, format, request.ResponseFormat)
		})
	}
}
