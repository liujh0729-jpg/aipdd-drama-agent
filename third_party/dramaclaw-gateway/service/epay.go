package service

import (
	"github.com/dramaclaw/localapi/setting/operation_setting"
	"github.com/dramaclaw/localapi/setting/system_setting"
)

func GetCallbackAddress() string {
	if operation_setting.CustomCallbackAddress == "" {
		return system_setting.ServerAddress
	}
	return operation_setting.CustomCallbackAddress
}
