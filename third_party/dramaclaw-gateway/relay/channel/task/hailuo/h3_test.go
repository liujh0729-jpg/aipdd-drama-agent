package hailuo

import (
	"bytes"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestH3VideoRequestTextToVideo(t *testing.T) {
	req := relaycommon.TaskSubmitReq{
		Model:    h3ModelName,
		Prompt:   "A cinematic city flythrough",
		Duration: 5,
		Width:    720,
		Height:   1280,
		Metadata: map[string]any{"resolution": "720p", "ratio": "9:16"},
	}

	body, err := h3VideoRequestFromTask(req)

	require.NoError(t, err)
	assert.Equal(t, h3ModelName, body.Model)
	assert.Equal(t, "768P", body.Resolution)
	assert.Equal(t, 5, body.Duration)
	assert.Equal(t, "9:16", body.Ratio)
	require.Len(t, body.Content, 1)
	assert.Equal(t, "text", body.Content[0].Type)
}

func TestH3VideoRequestTopLevelImageUsesFirstFrame(t *testing.T) {
	req := relaycommon.TaskSubmitReq{
		Model:    h3ModelName,
		Prompt:   "Animate",
		Image:    "https://example.com/first.png",
		Duration: 4,
		Metadata: map[string]any{"resolution": "2k", "ratio": "16:9"},
	}

	body, err := h3VideoRequestFromTask(req)

	require.NoError(t, err)
	assert.Equal(t, "adaptive", body.Ratio)
	require.Len(t, body.Content, 2)
	assert.Equal(t, "first_frame", body.Content[1].Role)
	require.NotNil(t, body.Content[1].ImageURL)
	assert.Equal(t, req.Image, body.Content[1].ImageURL.URL)
}

func TestH3VideoRequestSingleReferenceImagePreservesReferenceRole(t *testing.T) {
	req := relaycommon.TaskSubmitReq{
		Model:    h3ModelName,
		Prompt:   "Animate the reference",
		Duration: 5,
		Metadata: map[string]any{
			"reference_images": []string{"https://example.com/reference.png"},
			"resolution":       "768p",
		},
	}

	body, err := h3VideoRequestFromTask(req)

	require.NoError(t, err)
	require.Len(t, body.Content, 2)
	assert.Equal(t, "reference_image", body.Content[1].Role)
	assert.Equal(t, "https://example.com/reference.png", body.Content[1].ImageURL.URL)
	assert.Equal(t, "adaptive", body.Ratio)
}

func TestH3VideoRequestPreservesFirstLastFrameRoles(t *testing.T) {
	req := relaycommon.TaskSubmitReq{
		Prompt: "Transition",
		Image:  "first.png",
		Metadata: map[string]any{
			"last_frame_image": "last.png",
			"ratio":            "4:3",
		},
	}

	body, err := h3VideoRequestFromTask(req)

	require.NoError(t, err)
	require.Len(t, body.Content, 3)
	assert.Equal(t, "first_frame", body.Content[1].Role)
	assert.Equal(t, "last_frame", body.Content[2].Role)
	assert.Equal(t, "adaptive", body.Ratio)
}

func TestH3VideoRequestPreservesReferenceMediaRoles(t *testing.T) {
	req := relaycommon.TaskSubmitReq{
		Prompt: "Use all references",
		Metadata: map[string]any{
			"reference_images": []string{"one.png", "two.png"},
			"reference_videos": []string{"one.mp4"},
			"reference_audios": []string{"one.mp3"},
			"ratio":            "auto",
		},
	}

	body, err := h3VideoRequestFromTask(req)

	require.NoError(t, err)
	require.Len(t, body.Content, 5)
	assert.Equal(t, "reference_image", body.Content[1].Role)
	assert.Equal(t, "reference_image", body.Content[2].Role)
	assert.Equal(t, "reference_video", body.Content[3].Role)
	assert.Equal(t, "reference_audio", body.Content[4].Role)
	assert.Equal(t, "adaptive", body.Ratio)
}

func TestH3VideoRequestRejectsUnsupportedVideoEdit(t *testing.T) {
	req := relaycommon.TaskSubmitReq{
		Prompt:       "Edit",
		DurationAuto: true,
		DurationSet:  true,
		Metadata: map[string]any{
			"reference_videos": []string{"one.mp4"},
			"ratio":            "auto",
		},
	}

	_, err := h3VideoRequestFromTask(req)

	require.Error(t, err)
	assert.Contains(t, err.Error(), "does not support video editing")
}

func TestH3VideoRequestRejectsReferenceFileAndLink(t *testing.T) {
	tests := []struct {
		name     string
		metadata map[string]any
		field    string
	}{
		{name: "file", metadata: map[string]any{"reference_file": "https://example.com/brief.pdf"}, field: "metadata.reference_file"},
		{name: "link", metadata: map[string]any{"reference_link": "https://example.com/article"}, field: "metadata.reference_link"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := h3VideoRequestFromTask(relaycommon.TaskSubmitReq{
				Prompt:   "Use the reference",
				Duration: 5,
				Metadata: test.metadata,
			})

			var parameterErr *requestParameterError
			require.ErrorAs(t, err, &parameterErr)
			assert.Equal(t, test.field, parameterErr.field)
		})
	}
}

func TestH3VideoRequestRejectsExplicitTopLevelImages(t *testing.T) {
	var req relaycommon.TaskSubmitReq
	require.NoError(t, req.UnmarshalJSON([]byte(`{
		"prompt":"Animate",
		"images":["https://example.com/reference.png"]
	}`)))

	_, err := h3VideoRequestFromTask(req)

	require.Error(t, err)
	assert.Equal(t, "unsupported_media_field", relaycommon.DCMediaValidationErrorCode(err))
}

func TestH3AdaptorBuildsV2URL(t *testing.T) {
	adaptor := &TaskAdaptor{baseURL: "https://api.minimax.io"}
	info := &relaycommon.RelayInfo{ChannelMeta: &relaycommon.ChannelMeta{UpstreamModelName: h3ModelName}}

	requestURL, err := adaptor.BuildRequestURL(info)

	require.NoError(t, err)
	assert.Equal(t, "https://api.minimax.io/v2/video_generation", requestURL)
}

func TestH3CreateResponseKeepsPublicAndUpstreamTaskIDsSeparate(t *testing.T) {
	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	response := &http.Response{
		StatusCode: http.StatusOK,
		Body:       io.NopCloser(bytes.NewBufferString(`{"task_id":"upstream-123"}`)),
	}
	info := &relaycommon.RelayInfo{
		OriginModelName: "MiniMax-H3-Local",
		ChannelMeta:     &relaycommon.ChannelMeta{UpstreamModelName: h3ModelName},
		TaskRelayInfo:   &relaycommon.TaskRelayInfo{PublicTaskID: "task_public"},
	}

	upstreamID, _, taskErr := (&TaskAdaptor{}).DoResponse(context, response, info)

	require.Nil(t, taskErr)
	assert.Equal(t, "h3:upstream-123", upstreamID)
	assert.Contains(t, recorder.Body.String(), `"id":"task_public"`)
	assert.NotContains(t, recorder.Body.String(), "upstream-123")
}

func TestH3ParseAndConvertCompletedTask(t *testing.T) {
	data := []byte(`{
		"task": {
			"id":"upstream-123",
			"status":"succeeded",
			"content":{"url":"https://example.com/result.mp4"},
			"resolution":"2K",
			"duration":5,
			"ratio":"16:9"
		}
	}`)
	adaptor := &TaskAdaptor{}

	result, err := adaptor.ParseTaskResult(data)
	require.NoError(t, err)
	assert.Equal(t, model.TaskStatusSuccess, result.Status)
	assert.Equal(t, "https://example.com/result.mp4", result.Url)

	task := &model.Task{TaskID: "task_public", Status: model.TaskStatusSuccess, Data: data}
	task.Properties.OriginModelName = "MiniMax-H3-Local"
	converted, err := adaptor.ConvertToOpenAIVideo(task)
	require.NoError(t, err)
	assert.Contains(t, string(converted), `"id":"task_public"`)
	assert.Contains(t, string(converted), `"video_url":"https://example.com/result.mp4"`)
}
