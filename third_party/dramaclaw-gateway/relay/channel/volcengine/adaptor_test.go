package volcengine

import (
	"io"
	"net/http/httptest"
	"testing"

	"github.com/QuantumNous/new-api/common"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relay/constant"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestConvertAudioRequestBuildsVolcengineBasicSpeech(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	speed := 1.25

	reader, err := (&Adaptor{}).ConvertAudioRequest(c, &relaycommon.RelayInfo{
		RelayMode:       constant.RelayModeAudioSpeech,
		OriginModelName: "dramaclaw-tts-model",
		ChannelMeta: &relaycommon.ChannelMeta{
			ApiKey:            "app-id|access-token",
			UpstreamModelName: "volcano-tts-model",
		},
	}, dto.AudioRequest{
		Model:          "volcano-tts-model",
		Input:          "hello",
		Voice:          "custom-voice",
		ResponseFormat: "mp3",
		Speed:          &speed,
	})
	require.NoError(t, err)

	body, err := io.ReadAll(reader)
	require.NoError(t, err)
	var request VolcengineTTSRequest
	require.NoError(t, common.Unmarshal(body, &request))
	assert.Equal(t, "app-id", request.App.AppID)
	assert.Equal(t, "access-token", request.App.Token)
	assert.Equal(t, "hello", request.Request.Text)
	assert.Equal(t, "volcano-tts-model", request.Request.Model)
	assert.Equal(t, "custom-voice", request.Audio.VoiceType)
	assert.Equal(t, speed, request.Audio.SpeedRatio)
}

func TestConvertAudioRequestRejectsDCMediaExtensionsForVolcengine(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	metadata, err := common.Marshal(map[string]any{
		"audio_url": "https://example.com/reference.wav",
	})
	require.NoError(t, err)

	_, err = (&Adaptor{}).ConvertAudioRequest(c, &relaycommon.RelayInfo{
		RelayMode: constant.RelayModeAudioSpeech,
		ChannelMeta: &relaycommon.ChannelMeta{
			ApiKey: "app-id|access-token",
		},
	}, dto.AudioRequest{
		Model:    "volcano-tts-model",
		Input:    "hello",
		Metadata: metadata,
	})

	require.ErrorContains(t, err, "supports only basic speech requests")
}
