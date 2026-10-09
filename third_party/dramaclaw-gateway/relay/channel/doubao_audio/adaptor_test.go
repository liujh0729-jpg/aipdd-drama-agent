package doubao_audio

import (
	"encoding/base64"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	relayconstant "github.com/QuantumNous/new-api/relay/constant"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestConvertAudioRequestUsesConfiguredUpstreamModel(t *testing.T) {
	c := doubaoAudioTestContext()
	speed := 1.25
	reader, err := (&Adaptor{}).ConvertAudioRequest(c, &relaycommon.RelayInfo{
		RelayMode: relayconstant.RelayModeAudioSpeech,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: "channel-configured-model",
		},
	}, dto.AudioRequest{
		Model:          "dramaclaw-model-alias",
		Input:          "生成一段城市环境音",
		ResponseFormat: "opus",
		Speed:          &speed,
	})
	require.NoError(t, err)

	var request audioGenerationRequest
	require.NoError(t, decodeDoubaoAudioRequest(reader, &request))
	assert.Equal(t, "channel-configured-model", request.Model)
	assert.Equal(t, "生成一段城市环境音", request.TextPrompt)
	assert.Equal(t, "ogg_opus", request.AudioConfig.Format)
	require.NotNil(t, request.AudioConfig.SpeechRate)
	assert.Equal(t, 25, *request.AudioConfig.SpeechRate)
	assert.Empty(t, request.References)
}

func TestGetRequestURLAcceptsBaseAndFullEndpoint(t *testing.T) {
	adaptor := &Adaptor{}

	fromBase, err := adaptor.GetRequestURL(&relaycommon.RelayInfo{ChannelMeta: &relaycommon.ChannelMeta{
		ChannelBaseUrl: "https://speech.example.com/",
	}})
	require.NoError(t, err)
	assert.Equal(t, "https://speech.example.com/api/v3/tts/create", fromBase)

	fromEndpoint, err := adaptor.GetRequestURL(&relaycommon.RelayInfo{ChannelMeta: &relaycommon.ChannelMeta{
		ChannelBaseUrl: "https://speech.example.com/api/v3/tts/create",
	}})
	require.NoError(t, err)
	assert.Equal(t, "https://speech.example.com/api/v3/tts/create", fromEndpoint)
}

func TestConvertAudioRequestMapsCurrentReferenceAudioProfile(t *testing.T) {
	c := doubaoAudioTestContext()
	useEmotion := false
	metadata, err := common.Marshal(map[string]any{
		"audio_url":                     "data:audio/wav;base64,UklGRg==",
		"should_use_prompt_for_emotion": useEmotion,
		"emotion_prompt":                "紧张、急促",
	})
	require.NoError(t, err)

	reader, err := (&Adaptor{}).ConvertAudioRequest(c, &relaycommon.RelayInfo{
		RelayMode: relayconstant.RelayModeAudioSpeech,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: "seed-audio-compatible-model",
		},
	}, dto.AudioRequest{
		Model:          "local-audio-model",
		Input:          "你终于来了。",
		ResponseFormat: "mp3",
		Metadata:       metadata,
	})
	require.NoError(t, err)

	var request audioGenerationRequest
	require.NoError(t, decodeDoubaoAudioRequest(reader, &request))
	require.Len(t, request.References, 1)
	assert.Equal(t, "UklGRg==", request.References[0].AudioData)
	assert.Empty(t, request.References[0].AudioURL)
	assert.Contains(t, request.TextPrompt, "@音频1")
	assert.NotContains(t, request.TextPrompt, "紧张、急促")
}

func TestConvertAudioRequestMapsCurrentMusicProfile(t *testing.T) {
	c := doubaoAudioTestContext()
	metadata, err := common.Marshal(map[string]any{
		"music_length_ms":            30_000,
		"force_instrumental":         true,
		"respect_sections_durations": true,
		"output_format":              "mp3_44100_128",
	})
	require.NoError(t, err)

	reader, err := (&Adaptor{}).ConvertAudioRequest(c, &relaycommon.RelayInfo{
		RelayMode: relayconstant.RelayModeAudioSpeech,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: "configured-audio-generation-model",
		},
	}, dto.AudioRequest{
		Model:          "local-music-model",
		Input:          "克制的悬疑配乐",
		ResponseFormat: "mp3",
		Metadata:       metadata,
	})
	require.NoError(t, err)

	var request audioGenerationRequest
	require.NoError(t, decodeDoubaoAudioRequest(reader, &request))
	assert.Contains(t, request.TextPrompt, "30 秒")
	assert.Contains(t, request.TextPrompt, "纯音乐")
	assert.Contains(t, request.TextPrompt, "段落的时长安排")
	assert.Equal(t, "mp3", request.AudioConfig.Format)
}

func TestConvertAudioRequestRejectsMusicLongerThanProviderLimit(t *testing.T) {
	metadata, err := common.Marshal(map[string]any{
		"music_length_ms": maxOutputDurationMS + 1,
	})
	require.NoError(t, err)

	_, err = (&Adaptor{}).ConvertAudioRequest(doubaoAudioTestContext(), &relaycommon.RelayInfo{
		RelayMode: relayconstant.RelayModeAudioSpeech,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: "configured-audio-generation-model",
		},
	}, dto.AudioRequest{
		Model:          "local-music-model",
		Input:          "生成一段配乐",
		ResponseFormat: "mp3",
		Metadata:       metadata,
	})
	require.ErrorContains(t, err, "must not exceed 120000")
}

func TestConvertAudioRequestRejectsConflictingVoiceAndReferenceAudio(t *testing.T) {
	c := doubaoAudioTestContext()
	metadata, err := common.Marshal(map[string]any{
		"audio_url": "https://example.com/reference.mp3",
	})
	require.NoError(t, err)

	_, err = (&Adaptor{}).ConvertAudioRequest(c, &relaycommon.RelayInfo{
		RelayMode: relayconstant.RelayModeAudioSpeech,
	}, dto.AudioRequest{
		Model:          "configured-model",
		Input:          "测试",
		Voice:          "speaker-id",
		ResponseFormat: "mp3",
		Metadata:       metadata,
	})
	require.ErrorContains(t, err, "voice and metadata.audio_url cannot be combined")
}

func TestDoubaoAudioReferenceRejectsOversizedDataURL(t *testing.T) {
	payload := base64.StdEncoding.EncodeToString(make([]byte, maxReferenceAudioBytes+1))
	_, err := doubaoAudioReference("data:audio/wav;base64," + payload)
	require.ErrorContains(t, err, "exceeds maximum allowed size")
}

func TestDoubaoAudioReferenceRejectsInvalidBase64(t *testing.T) {
	_, err := doubaoAudioReference("data:audio/wav;base64,not-valid-***")
	require.ErrorContains(t, err, "invalid doubao audio base64 data")
}

func TestDoResponseReturnsAudioBinaryAndTracksDuration(t *testing.T) {
	recorder := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(recorder)
	c.Request = httptest.NewRequest(http.MethodPost, "/v1/audio/speech", nil)
	response := `{"code":0,"message":"success","audio":"` + base64.StdEncoding.EncodeToString([]byte("audio-data")) + `","duration":4.2,"original_duration":5.1}`
	resp := &http.Response{
		StatusCode: http.StatusOK,
		Header:     http.Header{"X-Tt-Logid": []string{"upstream-log-id"}},
		Body:       io.NopCloser(strings.NewReader(response)),
	}

	usage, relayErr := (&Adaptor{}).DoResponse(c, resp, &relaycommon.RelayInfo{})
	require.Nil(t, relayErr)
	assert.Equal(t, "audio-data", recorder.Body.String())
	assert.Equal(t, "upstream-log-id", c.GetString(common.UpstreamRequestIdKey))
	require.IsType(t, &dto.Usage{}, usage)
	assert.Equal(t, 6, usage.(*dto.Usage).CompletionTokens)
}

func TestDoResponseRejectsOverflowingDurationAndRecordsClamp(t *testing.T) {
	recorder := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(recorder)
	c.Request = httptest.NewRequest(http.MethodPost, "/v1/audio/speech", nil)
	resp := &http.Response{
		StatusCode: http.StatusOK,
		Header:     make(http.Header),
		Body: io.NopCloser(strings.NewReader(
			`{"code":0,"audio":"` + base64.StdEncoding.EncodeToString([]byte("audio-data")) + `","original_duration":1e300}`,
		)),
	}
	info := &relaycommon.RelayInfo{}

	usage, relayErr := (&Adaptor{}).DoResponse(c, resp, info)

	require.Nil(t, usage)
	require.NotNil(t, relayErr)
	assert.Contains(t, relayErr.Error(), "invalid original_duration")
	require.NotNil(t, info.QuotaClamp)
	assert.Equal(t, common.QuotaClampOverflow, info.QuotaClamp.Kind)
	assert.Equal(t, common.MaxQuota, info.QuotaClamp.Clamped)
	assert.Empty(t, recorder.Body.String())
}

func TestDoResponseReturnsCanonicalURL(t *testing.T) {
	recorder := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(recorder)
	c.Request = httptest.NewRequest(http.MethodPost, "/v1/audio/speech", nil)
	resp := &http.Response{
		StatusCode: http.StatusOK,
		Header:     make(http.Header),
		Body:       io.NopCloser(strings.NewReader(`{"code":0,"url":"https://example.com/result.mp3"}`)),
	}

	_, relayErr := (&Adaptor{}).DoResponse(c, resp, &relaycommon.RelayInfo{})
	require.Nil(t, relayErr)
	assert.JSONEq(t, `{"audio":{"url":"https://example.com/result.mp3"}}`, recorder.Body.String())
}

func TestDoErrorResponsePreservesProviderCodeAndLogID(t *testing.T) {
	recorder := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(recorder)
	c.Request = httptest.NewRequest(http.MethodPost, "/v1/audio/speech", nil)
	resp := &http.Response{
		StatusCode: http.StatusForbidden,
		Header:     http.Header{"X-Tt-Logid": []string{"provider-trace-id"}},
		Body:       io.NopCloser(strings.NewReader(`{"code":10074,"message":"requested resource not granted"}`)),
	}

	relayErr := (&Adaptor{}).DoErrorResponse(c, resp, &relaycommon.RelayInfo{})
	require.NotNil(t, relayErr)
	assert.Equal(t, "provider-trace-id", c.GetString(common.UpstreamRequestIdKey))
	assert.Contains(t, relayErr.Error(), "10074")
	assert.Contains(t, relayErr.Error(), "requested resource not granted")
}

func doubaoAudioTestContext() *gin.Context {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	c.Request = httptest.NewRequest(http.MethodPost, "/v1/audio/speech", nil)
	return c
}

func decodeDoubaoAudioRequest(reader io.Reader, target *audioGenerationRequest) error {
	body, err := io.ReadAll(reader)
	if err != nil {
		return err
	}
	return common.Unmarshal(body, target)
}
