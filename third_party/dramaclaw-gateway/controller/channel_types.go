package controller

import (
	"net/http"
	"strconv"
	"strings"

	"github.com/QuantumNous/new-api/relay"
	"github.com/gin-gonic/gin"
)

func GetChannelTypes(c *gin.Context) {
	capability := strings.ToLower(strings.TrimSpace(c.Query("capability")))
	keyword := strings.ToLower(strings.TrimSpace(c.Query("keyword")))
	statusFilter := -1
	if rawStatus := strings.TrimSpace(c.Query("status")); rawStatus != "" {
		status, err := strconv.Atoi(rawStatus)
		if err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"success": false, "message": "invalid status"})
			return
		}
		statusFilter = status
	}

	registered := relay.GetRegisteredChannelTypes()
	items := make([]relay.ChannelTypeMetadata, 0, len(registered))
	for _, item := range registered {
		if statusFilter >= 0 && item.Status != statusFilter {
			continue
		}
		if capability != "" && !containsCapability(item.Capabilities, capability) {
			continue
		}
		if keyword != "" {
			haystack := strings.ToLower(item.Provider + " " + item.Name + " " + item.Description)
			if !strings.Contains(haystack, keyword) {
				continue
			}
		}
		items = append(items, item)
	}
	c.JSON(http.StatusOK, gin.H{"success": true, "data": gin.H{"items": items}})
}

func containsCapability(capabilities []string, expected string) bool {
	for _, capability := range capabilities {
		if capability == expected {
			return true
		}
	}
	return false
}
