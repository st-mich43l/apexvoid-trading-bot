package kafka

import "errors"

// ClassifiedError distinguishes a transient processing failure (worth a
// bounded retry) from a permanent contract failure (never retry — log,
// count, and move past it; source task §19/§20). Broker/network-level
// retry is franz-go's own internal concern (ADR-008) and never reaches
// this type — this classification is purely about the APPLICATION
// handler's own outcome, one layer up from the Kafka client itself.
type ClassifiedError struct {
	Err       error
	Permanent bool
}

func (e *ClassifiedError) Error() string { return e.Err.Error() }
func (e *ClassifiedError) Unwrap() error { return e.Err }

// Permanent wraps err as a poison-message failure — decode/validation
// errors, and a Handler's own definitively-not-retryable outcomes (e.g.
// "symbol not registered," a condition stable for the life of the
// process), use this so the consumer commits past the record instead of
// retrying it forever (source task §19).
func Permanent(err error) error {
	if err == nil {
		return nil
	}
	return &ClassifiedError{Err: err, Permanent: true}
}

// Transient wraps err as worth a bounded retry — used by a Handler
// implementation that hits a genuinely temporary condition. Unclassified
// errors (a plain error, not wrapped via Permanent/Transient) default to
// transient — see IsPermanent — so a Handler returning a bare error
// never has its event silently skipped by default; it must opt IN to
// "never retry this," not opt OUT of retrying.
func Transient(err error) error {
	if err == nil {
		return nil
	}
	return &ClassifiedError{Err: err, Permanent: false}
}

// IsPermanent reports whether err was explicitly classified permanent.
// An unclassified error is treated as transient — see Transient's doc
// comment on why that is the safer default (source task §18: never
// silently skip a genuinely-transient failure).
func IsPermanent(err error) bool {
	var ce *ClassifiedError
	if errors.As(err, &ce) {
		return ce.Permanent
	}
	return false
}
