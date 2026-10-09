package relay

import (
	"net/url"
	"strconv"
	"testing"

	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/relay/channel/task/taskcommon"
	"github.com/stretchr/testify/require"
)

func TestTaskResponseResultURLSignsOnlyComfyUIProxy(t *testing.T) {
	comfyTask := &model.Task{
		TaskID:   "task_comfy",
		Platform: constant.TaskPlatform(strconv.Itoa(constant.ChannelTypeComfyUI)),
		PrivateData: model.TaskPrivateData{
			ResultURL:         "http://localhost:3000/v1/videos/task_comfy/content",
			UpstreamResultURL: "http://192.168.2.222:8188/view?filename=result.mp4",
		},
	}
	parsed, err := url.Parse(taskResponseResultURL(comfyTask))
	require.NoError(t, err)
	expires, err := strconv.ParseInt(parsed.Query().Get("expires"), 10, 64)
	require.NoError(t, err)
	require.Equal(t, "/v1/public/videos/task_comfy/content", parsed.Path)
	require.True(t, taskcommon.ValidatePublicProxySignature("task_comfy", expires, parsed.Query().Get("signature")))

	directTask := &model.Task{
		TaskID:   "task_direct",
		Platform: constant.TaskPlatform(strconv.Itoa(constant.ChannelTypeDoubaoVideo)),
		PrivateData: model.TaskPrivateData{
			ResultURL: "https://cdn.example.com/result.mp4",
		},
	}
	require.Equal(t, "https://cdn.example.com/result.mp4", taskResponseResultURL(directTask))
}

func TestTaskModel2DtoUsesSignedComfyUIProxy(t *testing.T) {
	task := &model.Task{
		TaskID:   "task_comfy_legacy_data",
		Platform: constant.TaskPlatform(strconv.Itoa(constant.ChannelTypeComfyUI)),
		PrivateData: model.TaskPrivateData{
			ResultURL:         "http://localhost:3000/v1/videos/task_comfy_legacy_data/content",
			UpstreamResultURL: "http://192.168.2.222:8188/view?filename=result.mp4",
		},
	}

	parsed, err := url.Parse(TaskModel2Dto(task).ResultURL)
	require.NoError(t, err)
	require.Equal(t, "/v1/public/videos/task_comfy_legacy_data/content", parsed.Path)
	require.NotEmpty(t, parsed.Query().Get("expires"))
	require.NotEmpty(t, parsed.Query().Get("signature"))
}
