package taskcommon

import (
	"net/url"
	"strconv"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestBuildPublicProxyURLIncludesValidSignature(t *testing.T) {
	rawURL := BuildPublicProxyURL("task_public_proxy")
	parsed, err := url.Parse(rawURL)
	require.NoError(t, err)
	expires, err := strconv.ParseInt(parsed.Query().Get("expires"), 10, 64)
	require.NoError(t, err)

	require.Equal(t, "/v1/public/videos/task_public_proxy/content", parsed.Path)
	require.Greater(t, expires, time.Now().Unix())
	require.True(t, ValidatePublicProxySignature("task_public_proxy", expires, parsed.Query().Get("signature")))
	require.False(t, ValidatePublicProxySignature("task_other", expires, parsed.Query().Get("signature")))
	require.False(t, ValidatePublicProxySignature("task_public_proxy", time.Now().Add(-time.Minute).Unix(), parsed.Query().Get("signature")))
}
