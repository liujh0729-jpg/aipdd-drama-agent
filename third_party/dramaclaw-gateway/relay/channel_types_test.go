package relay

import (
	"testing"

	"github.com/QuantumNous/new-api/constant"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestGetRegisteredChannelTypes(t *testing.T) {
	items := GetRegisteredChannelTypes()
	require.NotEmpty(t, items)
	previousType := 0
	providers := map[string]int{}
	for _, item := range items {
		assert.Greater(t, item.Type, previousType)
		assert.NotEmpty(t, item.Provider)
		assert.NotEmpty(t, item.Name)
		assert.Equal(t, ChannelTypeStatusEnabled, item.Status)
		assert.NotContains(t, providers, item.Provider, "duplicate provider %q for types %d and %d", item.Provider, providers[item.Provider], item.Type)
		providers[item.Provider] = item.Type
		previousType = item.Type
	}

	fal := channelTypeMetadataByType(t, items, constant.ChannelTypeFal)
	assert.Equal(t, "fal_ai", fal.Provider)
	assert.Equal(t, []string{"image", "video", "audio"}, fal.Capabilities)

	openRouter := channelTypeMetadataByType(t, items, constant.ChannelTypeOpenRouter)
	assert.Equal(t, "openrouter", openRouter.Provider)
	assert.Equal(t, []string{"text", "vision", "embedding"}, openRouter.Capabilities)

	doubaoAudio := channelTypeMetadataByType(t, items, constant.ChannelTypeDoubaoAudio)
	assert.Equal(t, "doubao_audio", doubaoAudio.Provider)
	assert.Equal(t, []string{"audio"}, doubaoAudio.Capabilities)
	assert.Equal(t, "https://openspeech.bytedance.com", doubaoAudio.DefaultBaseURL)
	assert.False(t, doubaoAudio.RequiresBaseURL)
	assert.True(t, doubaoAudio.SupportsBaseURLOverride)

	comfyUI := channelTypeMetadataByType(t, items, constant.ChannelTypeComfyUI)
	assert.Equal(t, "comfyui", comfyUI.Provider)
	assert.Equal(t, []string{"video"}, comfyUI.Capabilities)
}

func TestGetRegisteredChannelTypesOmitsUnregisteredTypes(t *testing.T) {
	for _, item := range GetRegisteredChannelTypes() {
		assert.NotEqual(t, constant.ChannelTypeUnknown, item.Type)
	}
}

func channelTypeMetadataByType(t *testing.T, items []ChannelTypeMetadata, channelType int) ChannelTypeMetadata {
	t.Helper()
	for _, item := range items {
		if item.Type == channelType {
			return item
		}
	}
	require.FailNowf(t, "channel type missing", "channel type %d was not registered", channelType)
	return ChannelTypeMetadata{}
}
