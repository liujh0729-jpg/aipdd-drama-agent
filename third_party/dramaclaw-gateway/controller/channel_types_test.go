package controller

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestGetChannelTypesFilters(t *testing.T) {
	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(recorder)
	c.Request = httptest.NewRequest(http.MethodGet, "/api/channel/types?capability=video&keyword=comfy", nil)

	GetChannelTypes(c)

	require.Equal(t, http.StatusOK, recorder.Code)
	assert.Contains(t, recorder.Body.String(), `"provider":"comfyui"`)
	assert.NotContains(t, recorder.Body.String(), `"provider":"openrouter"`)
}

func TestGetChannelTypesRejectsInvalidStatus(t *testing.T) {
	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(recorder)
	c.Request = httptest.NewRequest(http.MethodGet, "/api/channel/types?status=enabled", nil)

	GetChannelTypes(c)

	assert.Equal(t, http.StatusBadRequest, recorder.Code)
}

func TestGetChannelTypesFiltersFalMediaCapabilities(t *testing.T) {
	gin.SetMode(gin.TestMode)
	for _, capability := range []string{"image", "video", "audio"} {
		recorder := httptest.NewRecorder()
		c, _ := gin.CreateTestContext(recorder)
		c.Request = httptest.NewRequest(http.MethodGet, "/api/channel/types?capability="+capability+"&keyword=fal", nil)

		GetChannelTypes(c)

		require.Equal(t, http.StatusOK, recorder.Code)
		assert.Contains(t, recorder.Body.String(), `"provider":"fal_ai"`)
	}
}
