# 示例供应商适配器

本示例展示最小异步视频适配器的结构。实际实现可参考：

- `relay/channel/task/hailuo/h3.go`：多模态内容、公开任务 ID 与 H3 查询；
- `relay/channel/task/doubao/adaptor.go`：DC 素材角色到统一 `content` 的转换；
- `relay/channel/task/comfyui/`：Workflow 选择、素材上传、输入注入和排队任务取消。

## 骨架

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

## 素材映射

不要遍历一个无角色的图片数组后统一发送。应显式构造角色：

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

如果供应商只接受一张首帧图，收到 `reference_images` 时应明确失败，不能把第一张图
写入首帧字段，也不能忽略剩余图片。

## 提交与查询

`DoResponse` 解析供应商任务 ID，但响应给客户端的必须是 `info.PublicTaskID`。
查询通过 `FetchTask` 使用 `task.GetUpstreamTaskID()`，然后由 `ParseTaskResult` 返回
统一的 `relaycommon.TaskInfo`。

任务成功时把直接结果 URL 放入 `TaskInfo.Url`。若供应商只能提供文件 ID，适配器可在
查询阶段换取 URL，或让受控内容代理使用文件 ID 拉取结果。

## 取消

只有供应商提供按任务取消能力时才实现：

```go
var _ channel.TaskCanceller = (*TaskAdaptor)(nil)

func (a *TaskAdaptor) CancelTask(
    ctx context.Context,
    baseURL string,
    key string,
    upstreamTaskID string,
    proxy string,
) error {
    // 查询任务是否仍可取消。
    // 调用供应商的按任务取消接口。
    // 再次确认该任务已从运行或排队集合中移除。
    return nil
}
```

供应商只有“中断当前所有任务”一类全局接口时，不得实现该接口。
