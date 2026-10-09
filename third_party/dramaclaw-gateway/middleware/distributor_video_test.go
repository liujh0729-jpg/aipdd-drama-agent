package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"

	relayconstant "github.com/QuantumNous/new-api/relay/constant"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestGetModelRequestDoesNotSelectChannelForVideoCancellation(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	c.Request = httptest.NewRequest(http.MethodDelete, "/v1/videos/task_123", nil)
	c.Params = gin.Params{{Key: "task_id", Value: "task_123"}}

	modelRequest, shouldSelectChannel, err := getModelRequest(c)

	require.NoError(t, err)
	require.NotNil(t, modelRequest)
	require.False(t, shouldSelectChannel)
	require.Equal(t, relayconstant.RelayModeVideoFetchByID, c.GetInt("relay_mode"))
}
