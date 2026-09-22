package kafka

import "errors"

// ClassifiedError marks a malformed event-envelope/configuration error as
// non-retryable. It remains useful for the producer's strict codec even
// though analysis-engine no longer has a Kafka consumer.
type ClassifiedError struct {
	Err       error
	Permanent bool
}

func (e *ClassifiedError) Error() string { return e.Err.Error() }
func (e *ClassifiedError) Unwrap() error { return e.Err }

func Permanent(err error) error {
	if err == nil {
		return nil
	}
	return &ClassifiedError{Err: err, Permanent: true}
}

func IsPermanent(err error) bool {
	var classified *ClassifiedError
	return errors.As(err, &classified) && classified.Permanent
}
