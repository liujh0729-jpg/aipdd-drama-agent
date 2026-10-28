package service

import (
	"net/http/httptest"
	"testing"

	"github.com/dramaclaw/localapi/common"
	relaycommon "github.com/dramaclaw/localapi/relay/common"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func withBillingDisabled(t *testing.T) {
	t.Helper()
	prev := common.BillingDisabled
	common.BillingDisabled = true
	t.Cleanup(func() { common.BillingDisabled = prev })
}

// 交付形态不计费：即使预扣金额远超用户余额，预扣费/结算也必须是 no-op，
// 否则视频任务会被「预扣费额度失败」挡住。
func TestBillingDisabledSkipsPrechargeAndSettlement(t *testing.T) {
	withBillingDisabled(t)

	rec := httptest.NewRecorder()
	ctx, _ := gin.CreateTestContext(rec)
	info := &relaycommon.RelayInfo{UserId: 1, TokenId: 1, UsingGroup: "default"}

	require.Nil(t, PreConsumeBilling(ctx, 1_000_000_000, info))
	require.Nil(t, info.Billing)
	require.NoError(t, SettleBilling(ctx, info, 1_000_000_000))
}
