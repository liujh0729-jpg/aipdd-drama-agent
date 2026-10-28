package model

import (
	"testing"

	"github.com/dramaclaw/localapi/common"
	"github.com/stretchr/testify/require"
)

// 交付形态不计费：额度用尽的令牌仍然可用，只拒绝禁用/过期。
func TestValidateUserTokenAllowsExhaustedTokenWhenBillingDisabled(t *testing.T) {
	truncateTables(t)
	require.True(t, common.BillingDisabled)

	token := Token{
		UserId:      7,
		Key:         "sk-billing-disabled-exhausted",
		Name:        "exhausted",
		Status:      common.TokenStatusExhausted,
		RemainQuota: 0,
		ExpiredTime: -1,
	}
	require.NoError(t, token.Insert())

	got, err := ValidateUserToken(token.Key)
	require.NoError(t, err)
	require.EqualValues(t, 0, got.RemainQuota)

	require.NoError(t, DB.Model(&Token{}).Where("id = ?", got.Id).Update("status", common.TokenStatusDisabled).Error)
	_, err = ValidateUserToken(token.Key)
	require.ErrorIs(t, err, ErrTokenInvalid)
}
