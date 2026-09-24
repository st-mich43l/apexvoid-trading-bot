package engine

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

type publicationState string

const (
	publicationUnknown    publicationState = ""
	publicationSuppressed publicationState = "suppressed"
	publicationDeferred   publicationState = "deferred"
	publicationPending    publicationState = "pending"
	publicationPublished  publicationState = "published"
)

type publishJob struct {
	EventID    string                 `json:"event_id"`
	Symbol     market.Symbol          `json:"symbol"`
	Algorithm  kafka.AlgorithmVersion `json:"algorithm"`
	Transition opportunity.Transition `json:"transition"`
}

type publicationRecord struct {
	Creation publicationState `json:"creation"`
	Terminal publicationState `json:"terminal"`
	Deferred *publishJob      `json:"deferred_terminal,omitempty"`
}

type publicationLedger struct {
	Records map[string]publicationRecord `json:"records"`
	Queue   []publishJob                 `json:"queue"`
}

type publicationStore struct {
	path   string
	ledger publicationLedger
}

func openPublicationStore(path string) (*publicationStore, error) {
	store := &publicationStore{path: path, ledger: publicationLedger{Records: make(map[string]publicationRecord)}}
	if path == "" {
		return store, nil
	}
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return store, nil
		}
		return nil, fmt.Errorf("publication outbox: reading %s: %w", path, err)
	}
	if err := json.Unmarshal(data, &store.ledger); err != nil {
		return nil, fmt.Errorf("publication outbox: decoding %s: %w", path, err)
	}
	if store.ledger.Records == nil {
		store.ledger.Records = make(map[string]publicationRecord)
	}
	return store, nil
}

func (s *publicationStore) save() error {
	if s.path == "" {
		return nil
	}
	dir := filepath.Dir(s.path)
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return fmt.Errorf("publication outbox: creating %s: %w", dir, err)
	}
	tmp, err := os.CreateTemp(dir, ".publication-ledger-*")
	if err != nil {
		return fmt.Errorf("publication outbox: creating temporary ledger: %w", err)
	}
	tmpName := tmp.Name()
	ok := false
	defer func() {
		_ = tmp.Close()
		if !ok {
			_ = os.Remove(tmpName)
		}
	}()
	encoder := json.NewEncoder(tmp)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(s.ledger); err != nil {
		return fmt.Errorf("publication outbox: encoding ledger: %w", err)
	}
	if err := tmp.Sync(); err != nil {
		return fmt.Errorf("publication outbox: syncing ledger: %w", err)
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("publication outbox: closing ledger: %w", err)
	}
	if err := os.Rename(tmpName, s.path); err != nil {
		return fmt.Errorf("publication outbox: replacing ledger: %w", err)
	}
	directory, err := os.Open(dir)
	if err != nil {
		return fmt.Errorf("publication outbox: opening parent directory: %w", err)
	}
	if err := directory.Sync(); err != nil {
		_ = directory.Close()
		return fmt.Errorf("publication outbox: syncing parent directory: %w", err)
	}
	if err := directory.Close(); err != nil {
		return fmt.Errorf("publication outbox: closing parent directory: %w", err)
	}
	ok = true
	return nil
}
