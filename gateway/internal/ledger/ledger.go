// Package ledger is the Audit Service (docs/01 section 2, docs/02 section 9.1, [Go]).
//
// Local tamper-evident chain; not distributed, immutable, or a real blockchain. Ported from
// backend/app/ledger.py with the chain order and the legacy-record fallback preserved, so a
// database written by the Python build still verifies under the Go build.
package ledger

import (
	"crypto/hmac"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"

	_ "modernc.org/sqlite"
)

// GenesisPrev is the previous-hash of the first record.
const GenesisPrev = "0000000000000000000000000000000000000000000000000000000000000000"

// HashVersion marks records using the v2 chain order (payload then previous).
const HashVersion = "payload-prev-v2"

// Digest is the v2 chain order: canonical event bytes followed by the previous hash.
func Digest(previous, payload string) string {
	sum := sha256.Sum256([]byte(payload + previous))
	return hex.EncodeToString(sum[:])
}

// LegacyDigest is the v1 order, kept only so records written before the order was corrected
// still verify instead of reading as tampering.
func LegacyDigest(previous, payload string) string {
	sum := sha256.Sum256([]byte(previous + payload))
	return hex.EncodeToString(sum[:])
}

// Entry is one appended audit record as returned to callers.
type Entry struct {
	ID       int64          `json:"id"`
	Previous string         `json:"previous"`
	Hash     string         `json:"hash"`
	Fields   map[string]any `json:"-"`
}

// MarshalJSON flattens the event fields alongside the chain metadata, matching the Python shape
// the dashboard already renders.
func (e Entry) MarshalJSON() ([]byte, error) {
	out := make(map[string]any, len(e.Fields)+3)
	for k, v := range e.Fields {
		out[k] = v
	}
	out["id"] = e.ID
	out["previous"] = e.Previous
	out["hash"] = e.Hash
	return json.Marshal(out)
}

// Verification is the result of walking the chain.
type Verification struct {
	Valid   bool   `json:"valid"`
	Checked int    `json:"checked"`
	Hash    string `json:"hash,omitempty"`
	Reason  string `json:"reason,omitempty"`
}

// Ledger is a hash-chained append-only store backed by SQLite.
type Ledger struct {
	db *sql.DB

	// appends are serialised in-process as well as by BEGIN IMMEDIATE, because two goroutines
	// racing to read the tail hash would otherwise both chain onto the same predecessor and one
	// would lose its link.
	mu sync.Mutex

	// signingKey signs each record at origin (CLAUDE.md invariant 9). A bare content hash is not
	// enough: the store that holds the records must not be the thing that vouches for them.
	signingKey []byte
}

// Open creates or opens the ledger database.
func Open(path string, signingKey []byte) (*Ledger, error) {
	if dir := filepath.Dir(path); dir != "" && dir != "." {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return nil, fmt.Errorf("ledger directory: %w", err)
		}
	}
	db, err := sql.Open("sqlite", path+"?_pragma=busy_timeout(15000)")
	if err != nil {
		return nil, fmt.Errorf("open ledger: %w", err)
	}
	// synchronous=FULL costs write throughput and buys the thing that matters here: a record
	// that survives the power loss it was written to describe. docs/01 section 7.
	for _, pragma := range []string{"PRAGMA journal_mode=WAL", "PRAGMA synchronous=FULL"} {
		if _, err := db.Exec(pragma); err != nil {
			db.Close()
			return nil, fmt.Errorf("%s: %w", pragma, err)
		}
	}
	const schema = `CREATE TABLE IF NOT EXISTS ledger (
		id INTEGER PRIMARY KEY,
		previous TEXT NOT NULL,
		payload TEXT NOT NULL,
		hash TEXT NOT NULL UNIQUE)`
	if _, err := db.Exec(schema); err != nil {
		db.Close()
		return nil, fmt.Errorf("ledger schema: %w", err)
	}
	return &Ledger{db: db, signingKey: signingKey}, nil
}

// Close releases the database handle.
func (l *Ledger) Close() error { return l.db.Close() }

// Append writes one event, chaining it onto the current tail.
func (l *Ledger) Append(event map[string]any) (Entry, error) {
	l.mu.Lock()
	defer l.mu.Unlock()

	fields := make(map[string]any, len(event)+3)
	for k, v := range event {
		fields[k] = v
	}
	fields["hash_version"] = HashVersion
	fields["timestamp"] = time.Now().UTC().Format("2006-01-02T15:04:05.000000-07:00")

	payload, err := canonical(fields)
	if err != nil {
		return Entry{}, err
	}

	tx, err := l.db.Begin()
	if err != nil {
		return Entry{}, err
	}
	defer tx.Rollback()

	previous := GenesisPrev
	row := tx.QueryRow("SELECT hash FROM ledger ORDER BY id DESC LIMIT 1")
	var tail string
	switch err := row.Scan(&tail); err {
	case nil:
		previous = tail
	case sql.ErrNoRows:
	default:
		return Entry{}, err
	}

	// Sign before the store sees it, then chain. The signature covers the record's own bytes and
	// its link, so neither the payload nor its position can be changed without detection.
	fields["origin_signature"] = l.sign(payload + previous)
	if payload, err = canonical(fields); err != nil {
		return Entry{}, err
	}

	hash := Digest(previous, payload)
	res, err := tx.Exec("INSERT INTO ledger(previous,payload,hash) VALUES(?,?,?)", previous, payload, hash)
	if err != nil {
		return Entry{}, err
	}
	id, err := res.LastInsertId()
	if err != nil {
		return Entry{}, err
	}
	if err := tx.Commit(); err != nil {
		return Entry{}, err
	}
	return Entry{ID: id, Previous: previous, Hash: hash, Fields: fields}, nil
}

// Entries returns every record in chain order.
func (l *Ledger) Entries() ([]Entry, error) {
	rows, err := l.db.Query("SELECT id,previous,payload,hash FROM ledger ORDER BY id")
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []Entry
	for rows.Next() {
		var id int64
		var previous, payload, hash string
		if err := rows.Scan(&id, &previous, &payload, &hash); err != nil {
			return nil, err
		}
		var fields map[string]any
		if err := json.Unmarshal([]byte(payload), &fields); err != nil {
			return nil, err
		}
		out = append(out, Entry{ID: id, Previous: previous, Hash: hash, Fields: fields})
	}
	return out, rows.Err()
}

// Verify walks the chain from genesis. A nil target verifies the whole chain; a hash target stops
// once it is reached and reports whether everything up to it holds.
func (l *Ledger) Verify(target string) (Verification, error) {
	rows, err := l.db.Query("SELECT id,previous,payload,hash FROM ledger ORDER BY id")
	if err != nil {
		return Verification{}, err
	}
	defer rows.Close()

	previous := GenesisPrev
	n := 0
	for rows.Next() {
		n++
		var id int64
		var prev, payload, hash string
		if err := rows.Scan(&id, &prev, &payload, &hash); err != nil {
			return Verification{}, err
		}
		var fields map[string]any
		if err := json.Unmarshal([]byte(payload), &fields); err != nil {
			return Verification{Valid: false, Checked: n, Reason: "Chain integrity failure"}, nil
		}
		expected := LegacyDigest(prev, payload)
		if v, _ := fields["hash_version"].(string); v == HashVersion {
			expected = Digest(prev, payload)
		}
		if id != int64(n) || prev != previous || expected != hash {
			return Verification{Valid: false, Checked: n, Reason: "Chain integrity failure"}, nil
		}
		previous = hash
		if target != "" && target == hash {
			return Verification{Valid: true, Checked: n, Hash: hash}, nil
		}
	}
	if err := rows.Err(); err != nil {
		return Verification{}, err
	}
	if target != "" {
		return Verification{Valid: false, Checked: n, Reason: "Hash not found"}, nil
	}
	return Verification{Valid: true, Checked: n, Reason: "Chain verified"}, nil
}

// DB exposes the handle for tests that need to simulate tampering.
func (l *Ledger) DB() *sql.DB { return l.db }

func (l *Ledger) sign(payload string) string {
	mac := hmac.New(sha256.New, l.signingKey)
	mac.Write([]byte(payload))
	return "hmac-sha256:" + hex.EncodeToString(mac.Sum(nil))
}

// canonical serialises the event the same way the Python build did: sorted keys, no separator
// spaces, and an error rather than a NaN. encoding/json already sorts map keys and rejects
// non-finite floats, so this is a thin wrapper that names the requirement.
//
// Without a canonical form the same event could hash two ways and the chain would be
// unverifiable. Note that verification re-hashes the payload bytes as stored, never a
// re-serialisation, so a record written by the Python build still verifies here even though Go
// and Python format floats and escape non-ASCII differently.
func canonical(fields map[string]any) (string, error) {
	buf, err := json.Marshal(fields)
	if err != nil {
		return "", err
	}
	return string(buf), nil
}
