package doubao

import (
	"testing"

	"github.com/QuantumNous/new-api/common"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestConvertToRequestPayloadPreservesDCMediaRoles(t *testing.T) {
	req := relaycommon.TaskSubmitReq{
		Model:  "seedance-test",
		Prompt: "prompt",
		Metadata: map[string]any{
			"reference_images": []string{"https://example.com/ref-1.png", "https://example.com/ref-2.png"},
			"reference_videos": []string{"https://example.com/ref.mp4"},
			"reference_audios": []string{"https://example.com/ref.mp3"},
		},
	}

	payload, err := (&TaskAdaptor{}).convertToRequestPayload(&req)
	require.NoError(t, err)
	require.Len(t, payload.Content, 5)
	assert.Equal(t, "reference_image", payload.Content[0].Role)
	assert.Equal(t, "reference_image", payload.Content[1].Role)
	assert.Equal(t, "reference_video", payload.Content[2].Role)
	assert.Equal(t, "reference_audio", payload.Content[3].Role)
	assert.Equal(t, "text", payload.Content[4].Type)
}

func TestConvertToRequestPayloadKeepsSingleReferenceImageAsReference(t *testing.T) {
	req := relaycommon.TaskSubmitReq{
		Model:  "seedance-test",
		Prompt: "prompt",
		Metadata: map[string]any{
			"reference_images": []string{"https://example.com/ref.png"},
		},
	}

	payload, err := (&TaskAdaptor{}).convertToRequestPayload(&req)
	require.NoError(t, err)
	require.NotEmpty(t, payload.Content)
	assert.Equal(t, "reference_image", payload.Content[0].Role)
}

func TestConvertToRequestPayloadTranslatesCanonicalDurationAndRatio(t *testing.T) {
	req := relaycommon.TaskSubmitReq{
		Model:    "seedance-test",
		Prompt:   "prompt",
		Duration: 10,
		Metadata: map[string]any{"ratio": "auto"},
	}

	payload, err := (&TaskAdaptor{}).convertToRequestPayload(&req)
	require.NoError(t, err)
	require.NotNil(t, payload.Duration)
	assert.Equal(t, 10, int(*payload.Duration))
	assert.Equal(t, "adaptive", payload.Ratio)
}

func TestConvertToRequestPayloadForwardsOmniReferenceTaskType(t *testing.T) {
	req := relaycommon.TaskSubmitReq{
		Model:  "seedance-2.5",
		Prompt: "continue the video",
		Metadata: map[string]any{
			"omni_reference_task_type": "extend",
		},
	}

	payload, err := (&TaskAdaptor{}).convertToRequestPayload(&req)
	require.NoError(t, err)
	assert.Equal(t, "extend", payload.OmniReferenceTaskType)

	data, err := common.Marshal(payload)
	require.NoError(t, err)
	var body map[string]any
	require.NoError(t, common.Unmarshal(data, &body))
	assert.Equal(t, "extend", body["omni_reference_task_type"])
	assert.NotContains(t, body, "metadata")
}

func TestConvertToRequestPayloadPreservesSameURLAcrossFrameRoles(t *testing.T) {
	const frameURL = "https://example.com/loop.png"
	req := relaycommon.TaskSubmitReq{
		Model:  "seedance-test",
		Prompt: "seamless loop",
		Image:  frameURL,
		Metadata: map[string]any{
			"last_frame_image": frameURL,
		},
	}

	payload, err := (&TaskAdaptor{}).convertToRequestPayload(&req)
	require.NoError(t, err)
	require.Len(t, payload.Content, 3)
	assert.Equal(t, "first_frame", payload.Content[0].Role)
	assert.Equal(t, frameURL, payload.Content[0].ImageURL.URL)
	assert.Equal(t, "last_frame", payload.Content[1].Role)
	assert.Equal(t, frameURL, payload.Content[1].ImageURL.URL)
	assert.Equal(t, "text", payload.Content[2].Type)
}

func TestConvertToRequestPayloadIgnoresNonCanonicalImages(t *testing.T) {
	req := relaycommon.TaskSubmitReq{
		Model:  "seedance-test",
		Prompt: "prompt",
		Image:  "https://example.com/first.png",
		Images: []string{
			"https://example.com/first.png",
			"https://example.com/reference.png",
		},
	}

	payload, err := (&TaskAdaptor{}).convertToRequestPayload(&req)
	require.NoError(t, err)
	require.Len(t, payload.Content, 2)
	assert.Equal(t, "first_frame", payload.Content[0].Role)
	assert.Equal(t, "https://example.com/first.png", payload.Content[0].ImageURL.URL)
	assert.Equal(t, "text", payload.Content[1].Type)
}

func TestConvertToRequestPayloadIgnoresSupplierShapedMetadataContent(t *testing.T) {
	req := relaycommon.TaskSubmitReq{
		Model:  "seedance-test",
		Prompt: "prompt",
		Image:  "https://example.com/first.png",
		Metadata: map[string]any{
			"content": []map[string]any{
				{
					"type":      "image_url",
					"image_url": map[string]any{"url": "https://example.com/first.png"},
					"role":      "first_frame",
				},
			},
		},
	}

	payload, err := (&TaskAdaptor{}).convertToRequestPayload(&req)
	require.NoError(t, err)
	require.Len(t, payload.Content, 2)
	assert.Equal(t, "first_frame", payload.Content[0].Role)
	assert.Equal(t, "https://example.com/first.png", payload.Content[0].ImageURL.URL)
	assert.Equal(t, "text", payload.Content[1].Type)
}
