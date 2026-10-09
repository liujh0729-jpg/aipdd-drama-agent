package fal

import (
	"bytes"
	"encoding/base64"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/QuantumNous/new-api/common"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	relayconstant "github.com/QuantumNous/new-api/relay/constant"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/gin-gonic/gin"
)

func TestBuildFalTTSRequest(t *testing.T) {
	shouldUsePromptForEmotion := false
	strength := 0.75
	metadata, err := common.Marshal(map[string]any{
		"audio_url":                     "https://example.com/ref.mp3",
		"emotional_audio_url":           "https://example.com/emotion.mp3",
		"strength":                      strength,
		"should_use_prompt_for_emotion": shouldUsePromptForEmotion,
		"emotion_prompt":                "angry",
	})
	if err != nil {
		t.Fatalf("marshal metadata: %v", err)
	}

	got, err := buildFalTTSRequest(dto.AudioRequest{
		Input:    "hello",
		Metadata: metadata,
	})
	if err != nil {
		t.Fatalf("buildFalTTSRequest returned error: %v", err)
	}

	if got.AudioURL != "https://example.com/ref.mp3" {
		t.Fatalf("AudioURL = %q", got.AudioURL)
	}
	if got.Prompt != "hello" {
		t.Fatalf("Prompt = %q", got.Prompt)
	}
	if got.EmotionalAudioURL != "https://example.com/emotion.mp3" {
		t.Fatalf("EmotionalAudioURL = %q", got.EmotionalAudioURL)
	}
	if got.Strength == nil || *got.Strength != strength {
		t.Fatalf("Strength = %#v", got.Strength)
	}
	if got.ShouldUsePromptForEmotion == nil || *got.ShouldUsePromptForEmotion {
		t.Fatalf("ShouldUsePromptForEmotion = %#v", got.ShouldUsePromptForEmotion)
	}
	if got.EmotionPrompt != "angry" {
		t.Fatalf("EmotionPrompt = %q", got.EmotionPrompt)
	}
}

func TestBuildFalTTSRequestUsesInstructionsAsEmotionPrompt(t *testing.T) {
	metadata, err := common.Marshal(map[string]any{
		"audio_url": "https://example.com/ref.mp3",
	})
	if err != nil {
		t.Fatalf("marshal metadata: %v", err)
	}

	got, err := buildFalTTSRequest(dto.AudioRequest{
		Input:        "hello",
		Instructions: "scared",
		Metadata:     metadata,
	})
	if err != nil {
		t.Fatalf("buildFalTTSRequest returned error: %v", err)
	}
	if got.EmotionPrompt != "scared" {
		t.Fatalf("EmotionPrompt = %q", got.EmotionPrompt)
	}
	if got.ShouldUsePromptForEmotion == nil || !*got.ShouldUsePromptForEmotion {
		t.Fatalf("ShouldUsePromptForEmotion = %#v", got.ShouldUsePromptForEmotion)
	}
}

func TestConvertAudioRequest(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	metadata, err := common.Marshal(map[string]any{
		"audio_url": "https://example.com/ref.mp3",
	})
	if err != nil {
		t.Fatalf("marshal metadata: %v", err)
	}

	reader, err := (&Adaptor{}).ConvertAudioRequest(c, &relaycommon.RelayInfo{
		RelayMode: relayconstant.RelayModeAudioSpeech,
	}, dto.AudioRequest{
		Model:          ModelIndexTTS2,
		Input:          "hello",
		ResponseFormat: "mp3",
		Metadata:       metadata,
	})
	if err != nil {
		t.Fatalf("ConvertAudioRequest returned error: %v", err)
	}
	body, err := io.ReadAll(reader)
	if err != nil {
		t.Fatalf("read converted body: %v", err)
	}
	var got falTTSRequest
	if err := common.Unmarshal(body, &got); err != nil {
		t.Fatalf("unmarshal converted body: %v", err)
	}
	if got.AudioURL != "https://example.com/ref.mp3" || got.Prompt != "hello" {
		t.Fatalf("converted body = %#v", got)
	}
	if c.GetString(contextKeyResponseFormat) != "mp3" {
		t.Fatalf("response format context = %q", c.GetString(contextKeyResponseFormat))
	}
}

func TestConvertAudioRequestElevenLabsTTS(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	stability := 0.6
	timestamps := true
	metadata, err := common.Marshal(map[string]any{
		"stability":                stability,
		"timestamps":               timestamps,
		"language_code":            "en",
		"apply_text_normalization": "auto",
	})
	if err != nil {
		t.Fatalf("marshal metadata: %v", err)
	}

	reader, err := (&Adaptor{}).ConvertAudioRequest(c, &relaycommon.RelayInfo{
		RelayMode: relayconstant.RelayModeAudioSpeech,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: ModelElevenLabsTTSElevenV3ID,
		},
	}, dto.AudioRequest{
		Input:    "Hello world",
		Voice:    "Aria",
		Metadata: metadata,
	})
	if err != nil {
		t.Fatalf("ConvertAudioRequest returned error: %v", err)
	}
	body, err := io.ReadAll(reader)
	if err != nil {
		t.Fatalf("read converted body: %v", err)
	}
	var got falElevenLabsTTSRequest
	if err := common.Unmarshal(body, &got); err != nil {
		t.Fatalf("unmarshal converted body: %v", err)
	}
	if got.Text != "Hello world" {
		t.Fatalf("Text = %q", got.Text)
	}
	if got.Voice != "Aria" {
		t.Fatalf("Voice = %q", got.Voice)
	}
	if got.Stability == nil || *got.Stability != stability {
		t.Fatalf("Stability = %#v", got.Stability)
	}
	if got.Timestamps == nil || !*got.Timestamps {
		t.Fatalf("Timestamps = %#v", got.Timestamps)
	}
	if got.LanguageCode != "en" {
		t.Fatalf("LanguageCode = %q", got.LanguageCode)
	}
	if got.ApplyTextNormalization != "auto" {
		t.Fatalf("ApplyTextNormalization = %q", got.ApplyTextNormalization)
	}
}

func TestConvertAudioRequestElevenLabsMusic(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	musicLengthMS := 30000
	forceInstrumental := true
	respectSectionsDurations := false
	metadata, err := common.Marshal(map[string]any{
		"music_length_ms":            musicLengthMS,
		"force_instrumental":         forceInstrumental,
		"respect_sections_durations": respectSectionsDurations,
		"output_format":              "opus_48000_128",
		"composition_plan": map[string]any{
			"sections": []any{
				map[string]any{"name": "intro", "duration_ms": 5000},
			},
		},
	})
	if err != nil {
		t.Fatalf("marshal metadata: %v", err)
	}

	reader, err := (&Adaptor{}).ConvertAudioRequest(c, &relaycommon.RelayInfo{
		RelayMode: relayconstant.RelayModeAudioSpeech,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: ModelElevenLabsMusicID,
		},
	}, dto.AudioRequest{
		Input:    "Mysterious jungle soundtrack",
		Metadata: metadata,
	})
	if err != nil {
		t.Fatalf("ConvertAudioRequest returned error: %v", err)
	}
	body, err := io.ReadAll(reader)
	if err != nil {
		t.Fatalf("read converted body: %v", err)
	}
	var got falElevenLabsMusicRequest
	if err := common.Unmarshal(body, &got); err != nil {
		t.Fatalf("unmarshal converted body: %v", err)
	}
	if got.Prompt != "Mysterious jungle soundtrack" {
		t.Fatalf("Prompt = %q", got.Prompt)
	}
	if got.MusicLengthMS == nil || *got.MusicLengthMS != musicLengthMS {
		t.Fatalf("MusicLengthMS = %#v", got.MusicLengthMS)
	}
	if got.ForceInstrumental == nil || !*got.ForceInstrumental {
		t.Fatalf("ForceInstrumental = %#v", got.ForceInstrumental)
	}
	if got.RespectSectionsDurations == nil || *got.RespectSectionsDurations {
		t.Fatalf("RespectSectionsDurations = %#v", got.RespectSectionsDurations)
	}
	if got.OutputFormat != "opus_48000_128" {
		t.Fatalf("OutputFormat = %q", got.OutputFormat)
	}
	if len(got.CompositionPlan) == 0 {
		t.Fatalf("CompositionPlan is empty")
	}
	if c.GetString(contextKeyResponseFormat) != "opus" {
		t.Fatalf("response format context = %q", c.GetString(contextKeyResponseFormat))
	}
}

func TestConvertAudioRequestElevenLabsMusicMapsResponseFormat(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	metadata, err := common.Marshal(map[string]any{
		"music_length_ms": 30000,
	})
	if err != nil {
		t.Fatalf("marshal metadata: %v", err)
	}

	reader, err := (&Adaptor{}).ConvertAudioRequest(c, &relaycommon.RelayInfo{
		RelayMode: relayconstant.RelayModeAudioSpeech,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: ModelElevenLabsMusicID,
		},
	}, dto.AudioRequest{
		Input:          "Mysterious jungle soundtrack",
		ResponseFormat: "mp3",
		Metadata:       metadata,
	})
	if err != nil {
		t.Fatalf("ConvertAudioRequest returned error: %v", err)
	}
	body, err := io.ReadAll(reader)
	if err != nil {
		t.Fatalf("read converted body: %v", err)
	}
	var got falElevenLabsMusicRequest
	if err := common.Unmarshal(body, &got); err != nil {
		t.Fatalf("unmarshal converted body: %v", err)
	}
	if got.OutputFormat != "mp3_44100_128" {
		t.Fatalf("OutputFormat = %q", got.OutputFormat)
	}
	if c.GetString(contextKeyResponseFormat) != "mp3" {
		t.Fatalf("response format context = %q", c.GetString(contextKeyResponseFormat))
	}
}

func TestConvertAudioRequestElevenLabsMusicRejectsInvalidLength(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	metadata, err := common.Marshal(map[string]any{
		"music_length_ms": 1000,
	})
	if err != nil {
		t.Fatalf("marshal metadata: %v", err)
	}

	_, err = (&Adaptor{}).ConvertAudioRequest(c, &relaycommon.RelayInfo{
		RelayMode: relayconstant.RelayModeAudioSpeech,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: ModelElevenLabsMusicID,
		},
	}, dto.AudioRequest{
		Input:    "Mysterious jungle soundtrack",
		Metadata: metadata,
	})
	if err == nil {
		t.Fatalf("ConvertAudioRequest expected music_length_ms error")
	}
}

func TestConvertAudioRequestRejectsStream(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	metadata, err := common.Marshal(map[string]any{
		"audio_url": "https://example.com/ref.mp3",
	})
	if err != nil {
		t.Fatalf("marshal metadata: %v", err)
	}

	_, err = (&Adaptor{}).ConvertAudioRequest(c, &relaycommon.RelayInfo{
		RelayMode: relayconstant.RelayModeAudioSpeech,
	}, dto.AudioRequest{
		Input:        "hello",
		StreamFormat: "sse",
		Metadata:     metadata,
	})
	if err == nil {
		t.Fatalf("ConvertAudioRequest expected stream error")
	}
}

func TestGetRequestURLMapsShortModelID(t *testing.T) {
	got, err := (&Adaptor{}).GetRequestURL(&relaycommon.RelayInfo{
		RelayMode: relayconstant.RelayModeAudioSpeech,
		ChannelMeta: &relaycommon.ChannelMeta{
			ChannelBaseUrl:    "https://fal.run/",
			UpstreamModelName: ModelIndexTTS2,
		},
	})
	if err != nil {
		t.Fatalf("GetRequestURL returned error: %v", err)
	}
	want := "https://fal.run/fal-ai/index-tts-2/text-to-speech"
	if got != want {
		t.Fatalf("GetRequestURL = %q, want %q", got, want)
	}
}

func TestGetRequestURLMapsElevenLabsShortModelIDs(t *testing.T) {
	tests := []struct {
		name  string
		model string
		want  string
	}{
		{
			name:  "elevenlabs tts",
			model: ModelElevenLabsTTSElevenV3,
			want:  "https://fal.run/fal-ai/elevenlabs/tts/eleven-v3",
		},
		{
			name:  "elevenlabs music",
			model: ModelElevenLabsMusic,
			want:  "https://fal.run/fal-ai/elevenlabs/music",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := (&Adaptor{}).GetRequestURL(&relaycommon.RelayInfo{
				RelayMode: relayconstant.RelayModeAudioSpeech,
				ChannelMeta: &relaycommon.ChannelMeta{
					ChannelBaseUrl:    "https://fal.run/",
					UpstreamModelName: tt.model,
				},
			})
			if err != nil {
				t.Fatalf("GetRequestURL returned error: %v", err)
			}
			if got != tt.want {
				t.Fatalf("GetRequestURL = %q, want %q", got, tt.want)
			}
		})
	}
}

func TestDecodeDataURLAudio(t *testing.T) {
	payload := base64.StdEncoding.EncodeToString([]byte("audio"))
	got, contentType, err := decodeDataURLAudio("data:audio/mpeg;base64," + payload)
	if err != nil {
		t.Fatalf("decodeDataURLAudio returned error: %v", err)
	}
	if string(got) != "audio" {
		t.Fatalf("decoded payload = %q", string(got))
	}
	if contentType != "audio/mpeg" {
		t.Fatalf("contentType = %q", contentType)
	}
}

func TestFalAudioDurationExtPrefersMagicOverContentType(t *testing.T) {
	got := falAudioDurationExt([]byte("ID3\x04\x00\x00audio"), "audio/wav", "wav")
	if got != ".mp3" {
		t.Fatalf("duration ext = %q, want .mp3", got)
	}
}

func TestFalAudioDurationExtDetectsRIFFWave(t *testing.T) {
	got := falAudioDurationExt([]byte("RIFF\x00\x00\x00\x00WAVEfmt "), "audio/mpeg", "mp3")
	if got != ".wav" {
		t.Fatalf("duration ext = %q, want .wav", got)
	}
}

func TestHandleFalTTSResponseWithBase64Audio(t *testing.T) {
	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(recorder)
	c.Request = httptest.NewRequest(http.MethodPost, "/v1/audio/speech", nil)

	body, err := common.Marshal(map[string]any{
		"audio": base64.StdEncoding.EncodeToString([]byte("audio")),
	})
	if err != nil {
		t.Fatalf("marshal fal response: %v", err)
	}
	resp := &http.Response{
		StatusCode: http.StatusOK,
		Header:     make(http.Header),
		Body:       io.NopCloser(bytes.NewReader(body)),
	}

	usage, apiErr := handleFalTTSResponse(c, resp, &relaycommon.RelayInfo{
		TokenCountMeta: relaycommon.TokenCountMeta{},
	})
	if apiErr != nil {
		t.Fatalf("handleFalTTSResponse returned error: %v", apiErr)
	}
	if usage == nil {
		t.Fatalf("usage is nil")
	}
	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d", recorder.Code)
	}
	if recorder.Body.String() != "audio" {
		t.Fatalf("body = %q", recorder.Body.String())
	}
}
