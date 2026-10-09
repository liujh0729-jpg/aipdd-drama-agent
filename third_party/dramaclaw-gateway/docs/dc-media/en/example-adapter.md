# Example Provider Adapter

This example shows the structure of a minimal asynchronous video adapter. For
complete implementations, refer to:

- `relay/channel/task/hailuo/h3.go`: multimodal content, public task IDs, and H3 task queries;
- `relay/channel/task/doubao/adaptor.go`: conversion from DC media roles to unified `content` items;
- `relay/channel/task/comfyui/`: workflow selection, media upload, input injection, and queued-task cancellation.

## Skeleton

```go
type TaskAdaptor struct {
    taskcommon.BaseBilling
    baseURL string
    apiKey  string
}

func (a *TaskAdaptor) ValidateRequestAndSetAction(
    c *gin.Context,
    info *relaycommon.RelayInfo,
) *dto.TaskError {
    if taskErr := relaycommon.ValidateBasicTaskRequest(
        c,
        info,
        constant.TaskActionGenerate,
    ); taskErr != nil {
        return taskErr
    }

    req, err := relaycommon.GetTaskRequest(c)
    if err != nil {
        return service.TaskErrorWrapperLocal(err, "invalid_request", http.StatusBadRequest)
    }
    shape, err := relaycommon.ValidateDCMediaTaskRequest(&req)
    if err != nil {
        return service.TaskErrorWrapperLocal(
            err,
            relaycommon.DCMediaValidationErrorCode(err),
            http.StatusBadRequest,
        )
    }
    if shape == relaycommon.DCMediaVideoEdit {
        return service.TaskErrorWrapperLocal(
            errors.New("provider does not support video editing"),
            "unsupported_media_combination",
            http.StatusBadRequest,
        )
    }
    return nil
}
```

## Media Mapping

Do not iterate over an untyped image array and send every image with the same
meaning. Construct provider roles explicitly:

```go
metadata := relaycommon.DCMediaMetadata{}
if err := req.UnmarshalMetadata(&metadata); err != nil {
    return nil, err
}

appendImage(req.Image, "first_frame")
appendImage(metadata.LastFrameImage, "last_frame")
for _, image := range metadata.ReferenceImages {
    appendImage(image, "reference_image")
}
for _, video := range metadata.ReferenceVideos {
    appendVideo(video, "reference_video")
}
for _, audio := range metadata.ReferenceAudios {
    appendAudio(audio, "reference_audio")
}
```

If a provider accepts only one first-frame image, a request containing
`reference_images` must fail explicitly. Do not put the first reference image
into the first-frame field, and do not ignore the remaining images.

## Submission and Querying

`DoResponse` parses the provider task ID, but the response returned to the
client must contain `info.PublicTaskID`. `FetchTask` queries the provider with
`task.GetUpstreamTaskID()`, and `ParseTaskResult` returns a normalized
`relaycommon.TaskInfo`.

When the task succeeds, put a direct result URL in `TaskInfo.Url`. If the
provider returns only a file ID, the adapter may exchange it for a URL during a
query, or allow the controlled content proxy to retrieve the result by file ID.

## Cancellation

Implement cancellation only when the provider supports cancellation of a
specific task:

```go
var _ channel.TaskCanceller = (*TaskAdaptor)(nil)

func (a *TaskAdaptor) CancelTask(
    ctx context.Context,
    baseURL string,
    key string,
    upstreamTaskID string,
    proxy string,
) error {
    // Check whether the task can still be cancelled.
    // Call the provider's per-task cancellation endpoint.
    // Confirm that the task has left the running or queued set.
    return nil
}
```

Do not implement this interface when the provider offers only a global
"interrupt all current tasks" endpoint.
