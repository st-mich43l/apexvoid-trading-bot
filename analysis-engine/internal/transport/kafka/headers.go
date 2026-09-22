package kafka

import (
	"strconv"

	"github.com/twmb/franz-go/pkg/kgo"
)

// Kafka header names — kept minimal (source task §35): the JSON
// envelope remains authoritative for everything; headers exist only so
// a consumer-side filter can route or skip a record without a full JSON
// decode first.
const (
	HeaderEventType    = "event_type"
	HeaderEventVersion = "event_version"
	HeaderContentType  = "content_type"
	HeaderProducer     = "producer"
)

// ContentTypeJSON is the only content type this transport ever produces.
const ContentTypeJSON = "application/json"

// BuildHeaders returns the minimal header set for env — deliberately
// NOT a duplicate of every envelope field (source task §35).
func BuildHeaders(env Envelope) []kgo.RecordHeader {
	return []kgo.RecordHeader{
		{Key: HeaderEventType, Value: []byte(env.EventType)},
		{Key: HeaderEventVersion, Value: []byte(strconv.Itoa(env.EventVersion))},
		{Key: HeaderContentType, Value: []byte(ContentTypeJSON)},
		{Key: HeaderProducer, Value: []byte(env.Producer)},
	}
}
