package relay

import (
	"strconv"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/stretchr/testify/require"
)

func TestGetTaskAdaptorSupportsComfyUIChannelIDs(t *testing.T) {
	require.Equal(t, 63, constant.ChannelTypeComfyUI)
	require.NotNil(t, GetTaskAdaptor(constant.TaskPlatform(strconv.Itoa(constant.ChannelTypeComfyUI))))
}

func TestGetAdaptorsSupportFalChannel(t *testing.T) {
	require.Equal(t, 61, constant.ChannelTypeFal)
	apiType, ok := common.ChannelType2APIType(constant.ChannelTypeFal)
	require.True(t, ok)
	require.Equal(t, constant.APITypeFal, apiType)
	require.NotNil(t, GetAdaptor(constant.APITypeFal))
	require.NotNil(t, GetTaskAdaptor(constant.TaskPlatform(strconv.Itoa(constant.ChannelTypeFal))))
}

func TestGetAdaptorSupportsDoubaoAudioChannel(t *testing.T) {
	require.Equal(t, 62, constant.ChannelTypeDoubaoAudio)
	apiType, ok := common.ChannelType2APIType(constant.ChannelTypeDoubaoAudio)
	require.True(t, ok)
	require.Equal(t, constant.APITypeDoubaoAudio, apiType)
	require.NotNil(t, GetAdaptor(apiType))
}
