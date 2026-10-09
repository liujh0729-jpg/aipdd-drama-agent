package comfyui

import "net/http"

type taskBuildError struct {
	code       string
	message    string
	statusCode int
	data       any
}

func (e *taskBuildError) Error() string            { return e.message }
func (e *taskBuildError) TaskErrorCode() string    { return e.code }
func (e *taskBuildError) TaskErrorStatusCode() int { return e.statusCode }
func (e *taskBuildError) TaskErrorLocal() bool     { return true }
func (e *taskBuildError) TaskErrorData() any       { return e.data }

func comfyUIRequestError(message string, data any) error {
	return &taskBuildError{
		code:       "invalid_request",
		message:    message,
		statusCode: http.StatusBadRequest,
		data:       data,
	}
}

func comfyUIConfigurationError(message string, data any) error {
	return &taskBuildError{
		code:       "comfyui_configuration_error",
		message:    message,
		statusCode: http.StatusInternalServerError,
		data:       data,
	}
}
