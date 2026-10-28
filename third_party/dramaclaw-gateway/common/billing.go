package common

// BillingDisabled 关闭额度校验、预扣费、结算、退款与消费记账。
// LocalAPI 是随产品交付的本地模型中转网关：调用方只关心请求是否完成，
// 不存在余额、预扣费或结算概念，额度不足也不能阻断请求。
// 仅保留为 var，供计费账务的单元测试临时打开被旁路的代码路径。
var BillingDisabled = true
