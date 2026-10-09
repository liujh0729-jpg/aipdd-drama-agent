package fal

import (
	"net/http/httptest"
	"testing"

	"github.com/QuantumNous/new-api/common"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	relayconstant "github.com/QuantumNous/new-api/relay/constant"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestConvertAudioRequestRejectsUnsupportedFalModel(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())

	_, err := (&Adaptor{}).ConvertAudioRequest(c, &relaycommon.RelayInfo{
		RelayMode: relayconstant.RelayModeAudioSpeech,
	}, dto.AudioRequest{
		Model: "unknown-audio-model",
		Input: "hello",
	})

	require.ErrorContains(t, err, "unsupported fal audio model")
}

func TestConvertAudioRequestRejectsMismatchedFalAudioProfile(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())

	_, err := (&Adaptor{}).ConvertAudioRequest(c, &relaycommon.RelayInfo{
		RelayMode: relayconstant.RelayModeAudioSpeech,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: ModelElevenLabsMusicID,
		},
	}, dto.AudioRequest{
		Model: ModelElevenLabsMusic,
		Input: "soundtrack",
	})

	require.ErrorContains(t, err, "requires music request metadata")
}

func TestConvertAudioRequestRejectsReferenceMetadataForFalBasicTTS(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	metadata, err := common.Marshal(map[string]any{
		"audio_url": "https://example.com/reference.wav",
	})
	require.NoError(t, err)

	_, err = (&Adaptor{}).ConvertAudioRequest(c, &relaycommon.RelayInfo{
		RelayMode: relayconstant.RelayModeAudioSpeech,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: ModelElevenLabsTTSElevenV3ID,
		},
	}, dto.AudioRequest{
		Model:    ModelElevenLabsTTSElevenV3,
		Input:    "hello",
		Metadata: metadata,
	})

	require.ErrorContains(t, err, "requires speech request metadata")
}
