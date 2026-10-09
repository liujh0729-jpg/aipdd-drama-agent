#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_ROOT="${OUTPUT_ROOT:-$ROOT_DIR}"
PROVIDER="${PROVIDER:-}"
CHANNEL_TYPE="${TYPE:-}"
MODE="${MODE:-}"
DISPLAY_NAME="${NAME:-$PROVIDER}"
CAPABILITIES="${CAPABILITIES:-}"
GOFMT_BIN="${GOFMT_BIN:-gofmt}"

fail() {
  printf 'new-adapter: %s\n' "$1" >&2
  exit 1
}

if [[ -z "$PROVIDER" || -z "$CHANNEL_TYPE" || -z "$MODE" ]]; then
  fail 'usage: make new-adapter PROVIDER=example TYPE=64 MODE=task [NAME="Example"] [CAPABILITIES=image,video]'
fi

[[ "$PROVIDER" =~ ^[a-z][a-z0-9_]*$ ]] ||
  fail 'PROVIDER must start with a lowercase letter and contain only lowercase letters, digits, and underscores'
case "$PROVIDER" in
  break|default|func|interface|select|case|defer|go|map|struct|chan|else|goto|package|switch|const|fallthrough|if|range|type|continue|for|import|return|var)
    fail "PROVIDER must not be a Go keyword: $PROVIDER"
    ;;
esac
[[ "$CHANNEL_TYPE" =~ ^[1-9][0-9]*$ ]] || fail 'TYPE must be a positive integer'
[[ "$MODE" == "sync" || "$MODE" == "task" ]] || fail 'MODE must be sync or task'
[[ "$DISPLAY_NAME" =~ ^[A-Za-z0-9._[:space:]-]+$ ]] ||
  fail 'NAME may contain only letters, digits, spaces, dots, underscores, and hyphens'

if grep -Eq "ChannelType[A-Za-z0-9_]+[[:space:]]*=[[:space:]]*${CHANNEL_TYPE}([^0-9]|$)" "$ROOT_DIR/constant/channel.go"; then
  fail "TYPE ${CHANNEL_TYPE} is already assigned in constant/channel.go"
fi

if [[ -d "$OUTPUT_ROOT/relay/channel/$PROVIDER" ||
      -d "$OUTPUT_ROOT/relay/channel/task/$PROVIDER" ||
      -e "$OUTPUT_ROOT/docs/providers/$PROVIDER.md" ||
      -e "$OUTPUT_ROOT/docs/providers/en/$PROVIDER.md" ]]; then
  fail "provider ${PROVIDER} already has an adapter or provider document"
fi

if [[ -z "$CAPABILITIES" ]]; then
  if [[ "$MODE" == "task" ]]; then
    CAPABILITIES="video"
  else
    CAPABILITIES="image"
  fi
fi

capability_literal=""
IFS=',' read -r -a capability_items <<< "$CAPABILITIES"
for raw_capability in "${capability_items[@]}"; do
  capability="$(printf '%s' "$raw_capability" | tr '[:upper:]' '[:lower:]' | xargs)"
  case "$capability" in
    text|vision|embedding|rerank|image|video|audio) ;;
    *) fail "unsupported capability: ${raw_capability}" ;;
  esac
  if [[ -n "$capability_literal" ]]; then
    capability_literal+=", "
  fi
  capability_literal+="\"${capability}\""
done

if [[ "$MODE" == "task" ]]; then
  TARGET_ADAPTER_DIR="$OUTPUT_ROOT/relay/channel/task/$PROVIDER"
else
  TARGET_ADAPTER_DIR="$OUTPUT_ROOT/relay/channel/$PROVIDER"
fi
TARGET_DOC_FILE="$OUTPUT_ROOT/docs/providers/$PROVIDER.md"
TARGET_DOC_EN_FILE="$OUTPUT_ROOT/docs/providers/en/$PROVIDER.md"

STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dramaclaw-new-adapter.XXXXXX")"
ADAPTER_DIR="$STAGE_DIR/adapter"
DOC_FILE="$STAGE_DIR/provider.md"
DOC_EN_FILE="$STAGE_DIR/provider.en.md"
PUBLISHED_ADAPTER=0
PUBLISHED_DOC=0
PUBLISHED_DOC_EN=0
PUBLISH_COMPLETE=0

cleanup() {
  if [[ "$PUBLISH_COMPLETE" -ne 1 ]]; then
    if [[ "$PUBLISHED_DOC_EN" -eq 1 ]]; then
      rm -f "$TARGET_DOC_EN_FILE"
    fi
    if [[ "$PUBLISHED_DOC" -eq 1 ]]; then
      rm -f "$TARGET_DOC_FILE"
    fi
    if [[ "$PUBLISHED_ADAPTER" -eq 1 ]]; then
      rm -rf "$TARGET_ADAPTER_DIR"
    fi
  fi
  rm -rf "$STAGE_DIR"
}
trap cleanup EXIT

mkdir -p "$ADAPTER_DIR"

cat > "$ADAPTER_DIR/constants.go" <<EOF
package $PROVIDER

const (
	ChannelName         = "$PROVIDER"
	SuggestedChannelType = $CHANNEL_TYPE
)

var ModelList = []string{}
EOF

if [[ "$MODE" == "task" ]]; then
  cat > "$ADAPTER_DIR/adaptor.go" <<EOF
package $PROVIDER

import (
	"errors"
	"io"
	"net/http"

	taskdto "github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/relay/channel"
	taskcommon "github.com/QuantumNous/new-api/relay/channel/task/taskcommon"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/gin-gonic/gin"
)

var errNotImplemented = errors.New("$PROVIDER task adapter is not implemented")

type TaskAdaptor struct {
	taskcommon.BaseBilling
}

var _ channel.TaskAdaptor = (*TaskAdaptor)(nil)
var _ channel.CapabilityMetadataProvider = (*TaskAdaptor)(nil)

func (a *TaskAdaptor) Init(_ *relaycommon.RelayInfo) {}

func (a *TaskAdaptor) ValidateRequestAndSetAction(_ *gin.Context, _ *relaycommon.RelayInfo) *taskdto.TaskError {
	return &taskdto.TaskError{
		Code:       "not_implemented",
		Message:    errNotImplemented.Error(),
		StatusCode: http.StatusNotImplemented,
		LocalError: true,
		Error:      errNotImplemented,
	}
}

func (a *TaskAdaptor) BuildRequestURL(_ *relaycommon.RelayInfo) (string, error) {
	return "", errNotImplemented
}

func (a *TaskAdaptor) BuildRequestHeader(_ *gin.Context, _ *http.Request, _ *relaycommon.RelayInfo) error {
	return errNotImplemented
}

func (a *TaskAdaptor) BuildRequestBody(_ *gin.Context, _ *relaycommon.RelayInfo) (io.Reader, error) {
	return nil, errNotImplemented
}

func (a *TaskAdaptor) DoRequest(_ *gin.Context, _ *relaycommon.RelayInfo, _ io.Reader) (*http.Response, error) {
	return nil, errNotImplemented
}

func (a *TaskAdaptor) DoResponse(_ *gin.Context, _ *http.Response, _ *relaycommon.RelayInfo) (string, []byte, *taskdto.TaskError) {
	return "", nil, &taskdto.TaskError{
		Code:       "not_implemented",
		Message:    errNotImplemented.Error(),
		StatusCode: http.StatusNotImplemented,
		LocalError: true,
		Error:      errNotImplemented,
	}
}

func (a *TaskAdaptor) FetchTask(_, _ string, _ map[string]any, _ string) (*http.Response, error) {
	return nil, errNotImplemented
}

func (a *TaskAdaptor) ParseTaskResult(_ []byte) (*relaycommon.TaskInfo, error) {
	return nil, errNotImplemented
}

func (a *TaskAdaptor) GetModelList() []string { return ModelList }

func (a *TaskAdaptor) GetChannelName() string { return ChannelName }

func (a *TaskAdaptor) GetCapabilities() []string {
	return []string{$capability_literal}
}
EOF
else
  cat > "$ADAPTER_DIR/adaptor.go" <<EOF
package $PROVIDER

import (
	"errors"
	"io"
	"net/http"

	"github.com/QuantumNous/new-api/relay/channel"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/QuantumNous/new-api/relaykit/types"
	"github.com/gin-gonic/gin"
)

var errNotImplemented = errors.New("$PROVIDER sync adapter is not implemented")

type Adaptor struct{}

var _ channel.Adaptor = (*Adaptor)(nil)
var _ channel.CapabilityMetadataProvider = (*Adaptor)(nil)
var _ channel.BaseURLMetadataProvider = (*Adaptor)(nil)

func (a *Adaptor) Init(_ *relaycommon.RelayInfo) {}

func (a *Adaptor) GetRequestURL(_ *relaycommon.RelayInfo) (string, error) {
	return "", errNotImplemented
}

func (a *Adaptor) SetupRequestHeader(_ *gin.Context, _ *http.Header, _ *relaycommon.RelayInfo) error {
	return errNotImplemented
}

func (a *Adaptor) ConvertOpenAIRequest(_ *gin.Context, _ *relaycommon.RelayInfo, _ *dto.GeneralOpenAIRequest) (any, error) {
	return nil, errNotImplemented
}

func (a *Adaptor) ConvertRerankRequest(_ *gin.Context, _ int, _ dto.RerankRequest) (any, error) {
	return nil, errNotImplemented
}

func (a *Adaptor) ConvertEmbeddingRequest(_ *gin.Context, _ *relaycommon.RelayInfo, _ dto.EmbeddingRequest) (any, error) {
	return nil, errNotImplemented
}

func (a *Adaptor) ConvertAudioRequest(_ *gin.Context, _ *relaycommon.RelayInfo, _ dto.AudioRequest) (io.Reader, error) {
	return nil, errNotImplemented
}

func (a *Adaptor) ConvertImageRequest(_ *gin.Context, _ *relaycommon.RelayInfo, _ dto.ImageRequest) (any, error) {
	return nil, errNotImplemented
}

func (a *Adaptor) ConvertOpenAIResponsesRequest(_ *gin.Context, _ *relaycommon.RelayInfo, _ dto.OpenAIResponsesRequest) (any, error) {
	return nil, errNotImplemented
}

func (a *Adaptor) ConvertClaudeRequest(_ *gin.Context, _ *relaycommon.RelayInfo, _ *dto.ClaudeRequest) (any, error) {
	return nil, errNotImplemented
}

func (a *Adaptor) ConvertGeminiRequest(_ *gin.Context, _ *relaycommon.RelayInfo, _ *dto.GeminiChatRequest) (any, error) {
	return nil, errNotImplemented
}

func (a *Adaptor) DoRequest(_ *gin.Context, _ *relaycommon.RelayInfo, _ io.Reader) (any, error) {
	return nil, errNotImplemented
}

func (a *Adaptor) DoResponse(_ *gin.Context, _ *http.Response, _ *relaycommon.RelayInfo) (any, *types.NewAPIError) {
	return nil, types.NewOpenAIError(errNotImplemented, types.ErrorCodeInvalidRequest, http.StatusNotImplemented)
}

func (a *Adaptor) GetModelList() []string { return ModelList }

func (a *Adaptor) GetChannelName() string { return ChannelName }

func (a *Adaptor) GetCapabilities() []string {
	return []string{$capability_literal}
}

func (a *Adaptor) GetBaseURLPolicy() (bool, bool) {
	return true, true
}
EOF
fi

if [[ "$MODE" == "task" ]]; then
  cat > "$ADAPTER_DIR/adaptor_test.go" <<EOF
package $PROVIDER

import (
	"reflect"
	"testing"
)

func TestAdapterMetadata(t *testing.T) {
	adaptor := &TaskAdaptor{}
	gotName := adaptor.GetChannelName()
	gotCapabilities := adaptor.GetCapabilities()
	if gotName != ChannelName {
		t.Fatalf("GetChannelName() = %q, want %q", gotName, ChannelName)
	}
	wantCapabilities := []string{$capability_literal}
	if !reflect.DeepEqual(gotCapabilities, wantCapabilities) {
		t.Fatalf("GetCapabilities() = %v, want %v", gotCapabilities, wantCapabilities)
	}
}
EOF
else
  cat > "$ADAPTER_DIR/adaptor_test.go" <<EOF
package $PROVIDER

import (
	"reflect"
	"testing"
)

func TestAdapterMetadata(t *testing.T) {
	adaptor := &Adaptor{}
	gotName := adaptor.GetChannelName()
	gotCapabilities := adaptor.GetCapabilities()
	if gotName != ChannelName {
		t.Fatalf("GetChannelName() = %q, want %q", gotName, ChannelName)
	}
	wantCapabilities := []string{$capability_literal}
	if !reflect.DeepEqual(gotCapabilities, wantCapabilities) {
		t.Fatalf("GetCapabilities() = %v, want %v", gotCapabilities, wantCapabilities)
	}
}
EOF
fi

cat > "$DOC_FILE" <<EOF
# $DISPLAY_NAME

> 骨架状态：尚未实现。在以下转换测试和端到端验证通过前，不得将此渠道标记为已验证。

## 注册信息

- Provider ID：\`$PROVIDER\`
- 建议渠道类型：\`$CHANNEL_TYPE\`
- 适配器模式：\`$MODE\`
- 声明能力：\`$CAPABILITIES\`
- 官方文档：TODO
- 验证日期：TODO

## 支持模型与限制

| 上游模型 ID | 场景 | 比例/分辨率 | 时长 | 素材限制 |
|---|---|---|---|---|
| TODO | TODO | TODO | TODO | TODO |

## DC-Media 映射

记录供应商如何消费首帧、尾帧、参考图片、参考视频、参考音频、比例、分辨率、时长和
显式布尔值。不支持的组合必须在发送上游请求前返回错误。

## 注册检查清单

- [ ] 在 \`constant/channel.go\` 的 \`ChannelTypeDummy\` 之前增加 \`ChannelType<ProviderName> = $CHANNEL_TYPE\`。
- [ ] 在 \`constant/channel.go\` 增加显示名称和默认 Base URL。
- [ ] 同步适配器增加 API Type 映射和工厂注册。
- [ ] 异步适配器在 \`relay/relay_adaptor.go\` 增加任务工厂注册。
- [ ] 更新 \`docs/providers/README.md\` 及其英文版。
- [ ] 将所有 \`not implemented\` 路径替换为供应商实现。
- [ ] 增加转换、边界、错误、任务状态和结果测试。
- [ ] 使用供应商账号和 DramaClaw 完成脱敏端到端验证。

## 已知缺口

- TODO
EOF

cat > "$DOC_EN_FILE" <<EOF
# $DISPLAY_NAME

> Scaffold status: not implemented. Do not mark this provider as verified until
> the conversion tests and end-to-end checks below pass.

## Registration

- Provider ID: \`$PROVIDER\`
- Suggested channel type: \`$CHANNEL_TYPE\`
- Adapter mode: \`$MODE\`
- Declared capabilities: \`$CAPABILITIES\`
- Official documentation: TODO
- Verification date: TODO

## Supported Models and Limits

| Upstream model ID | Scenario | Ratio/resolution | Duration | Media limits |
|---|---|---|---|---|
| TODO | TODO | TODO | TODO | TODO |

## DC-Media Mapping

Document how the provider consumes first frame, last frame, reference images,
reference videos, reference audio, ratio, resolution, duration, and explicit
boolean values. Unsupported combinations must return an error before an
upstream request is sent.

## Registration Checklist

- [ ] Add a \`ChannelType<ProviderName> = $CHANNEL_TYPE\` constant before \`ChannelTypeDummy\` in \`constant/channel.go\`.
- [ ] Add the display name and default Base URL in \`constant/channel.go\`.
- [ ] For a sync adapter, add API type mapping and factory registration.
- [ ] For a task adapter, add task factory registration in \`relay/relay_adaptor.go\`.
- [ ] Update \`docs/providers/README.md\` and its Chinese version.
- [ ] Replace all \`not implemented\` paths with provider behavior.
- [ ] Add conversion, boundary, error, task-state, and result tests.
- [ ] Run a sanitized provider and DramaClaw end-to-end verification.

## Known Gaps

- TODO
EOF

"$GOFMT_BIN" -w "$ADAPTER_DIR/adaptor.go" "$ADAPTER_DIR/adaptor_test.go" "$ADAPTER_DIR/constants.go"

if [[ -d "$OUTPUT_ROOT/relay/channel/$PROVIDER" ||
      -d "$OUTPUT_ROOT/relay/channel/task/$PROVIDER" ||
      -e "$TARGET_DOC_FILE" ||
      -e "$TARGET_DOC_EN_FILE" ]]; then
  fail "provider ${PROVIDER} was created while the scaffold was being prepared"
fi

mkdir -p "$(dirname "$TARGET_ADAPTER_DIR")" "$(dirname "$TARGET_DOC_FILE")" "$(dirname "$TARGET_DOC_EN_FILE")"
mv "$ADAPTER_DIR" "$TARGET_ADAPTER_DIR"
PUBLISHED_ADAPTER=1
mv "$DOC_FILE" "$TARGET_DOC_FILE"
PUBLISHED_DOC=1
mv "$DOC_EN_FILE" "$TARGET_DOC_EN_FILE"
PUBLISHED_DOC_EN=1
PUBLISH_COMPLETE=1

printf 'Created %s adapter scaffold for %s.\n' "$MODE" "$PROVIDER"
printf '  %s\n' "${TARGET_ADAPTER_DIR#$OUTPUT_ROOT/}/adaptor.go"
printf '  %s\n' "${TARGET_ADAPTER_DIR#$OUTPUT_ROOT/}/adaptor_test.go"
printf '  %s\n' "${TARGET_ADAPTER_DIR#$OUTPUT_ROOT/}/constants.go"
printf '  %s\n' "${TARGET_DOC_FILE#$OUTPUT_ROOT/}"
printf '  %s\n' "${TARGET_DOC_EN_FILE#$OUTPUT_ROOT/}"
printf '\nNo shared registry was changed. Complete the checklist in %s before registering the adapter.\n' "${TARGET_DOC_FILE#$OUTPUT_ROOT/}"
