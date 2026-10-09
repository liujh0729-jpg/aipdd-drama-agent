package relay

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	rootcommon "github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestRelayTaskSubmitValidatesMappedH3Model(t *testing.T) {
	gin.SetMode(gin.TestMode)
	request := httptest.NewRequest(http.MethodPost, "/v1/video/generations", strings.NewReader(`{
		"model":"MiniMax-H3-Local",
		"prompt":"edit this video",
		"duration":"auto",
		"metadata":{
			"ratio":"auto",
			"reference_videos":["https://example.com/reference.mp4"]
		}
	}`))
	request.Header.Set("Content-Type", "application/json")
	context, _ := gin.CreateTestContext(httptest.NewRecorder())
	context.Request = request
	context.Set("platform", "35")
	context.Set("model_mapping", `{"MiniMax-H3-Local":"MiniMax-H3"}`)
	rootcommon.SetContextKey(context, constant.ContextKeyChannelType, constant.ChannelTypeMiniMax)
	rootcommon.SetContextKey(context, constant.ContextKeyOriginalModel, "MiniMax-H3-Local")

	info := &relaycommon.RelayInfo{
		OriginModelName: "MiniMax-H3-Local",
		TaskRelayInfo:   &relaycommon.TaskRelayInfo{},
	}
	result, taskErr := RelayTaskSubmit(context, info)

	require.Nil(t, result)
	require.NotNil(t, taskErr)
	require.Equal(t, "unsupported_media_combination", taskErr.Code)
	require.Equal(t, "MiniMax-H3", info.UpstreamModelName)
}
