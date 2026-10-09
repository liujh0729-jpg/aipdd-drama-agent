package relay

import (
	"testing"

	"github.com/QuantumNous/new-api/model"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestBuildDCMediaTaskResponseIncludesCanonicalFields(t *testing.T) {
	task := &model.Task{
		TaskID:   "task_public",
		Status:   model.TaskStatusSuccess,
		Progress: "100%",
		Properties: model.Properties{
			OriginModelName: "example-video-model",
		},
		PrivateData: model.TaskPrivateData{ResultURL: "https://example.com/result.mp4"},
	}
	response := buildDCMediaTaskResponse(task)
	assert.Equal(t, task.TaskID, response["id"])
	assert.Equal(t, task.TaskID, response["task_id"])
	assert.Equal(t, "succeeded", response["status"])
	assert.Equal(t, 100, response["progress"])
	require.Len(t, response["results"], 1)
	assert.Equal(t, "https://example.com/result.mp4", response["result_url"])
	assert.NotContains(t, response, "code")
	assert.NotContains(t, response, "data")
}

func TestBuildDCMediaTaskResponseUsesStableTerminalStates(t *testing.T) {
	assert.Equal(t, "cancelled", mapTaskStatusToDCMedia(model.TaskStatusCancelled))
	assert.Equal(t, "failed", mapTaskStatusToDCMedia(model.TaskStatusFailure))
	assert.Equal(t, "running", mapTaskStatusToDCMedia(model.TaskStatusInProgress))
	assert.Equal(t, 100, taskProgressPercent("125%"))
}
