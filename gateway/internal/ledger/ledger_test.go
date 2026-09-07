package ledger

import (
	"path/filepath"
	"strings"
	"testing"
)

func open(t *testing.T) *Ledger {
	t.Helper()
	l, err := Open(filepath.Join(t.TempDir(), "ledger.db"), []byte("test-signing-key"))
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	t.Cleanup(func() { l.Close() })
	return l
}

// docs/02 section 9.1: a record cannot be altered after the fact without breaking the chain.
func TestTamperingBreaksTheChain(t *testing.T) {
	l := open(t)
	if _, err := l.Append(map[string]any{"risk_score": 20}); err != nil {
		t.Fatalf("append: %v", err)
	}
	second, err := l.Append(map[string]any{"risk_score": 70})
	if err != nil {
		t.Fatalf("append: %v", err)
	}

	v, err := l.Verify(second.Hash)
	if err != nil || !v.Valid {
		t.Fatalf("intact chain did not verify: %+v (%v)", v, err)
	}

	if _, err := l.DB().Exec(`UPDATE ledger SET payload=? WHERE id=1`, `{"risk_score":99}`); err != nil {
		t.Fatalf("tamper: %v", err)
	}
	v, err = l.Verify(second.Hash)
	if err != nil {
		t.Fatalf("verify: %v", err)
	}
	if v.Valid {
		t.Error("a rewritten payload still verified; the chain is not tamper-evident")
	}
	if v.Checked != 1 {
		t.Errorf("verification stopped at record %d, want 1 — the break is at the first record", v.Checked)
	}
}

func TestUnknownHashIsNotValid(t *testing.T) {
	l := open(t)
	if _, err := l.Append(map[string]any{"risk_score": 20}); err != nil {
		t.Fatalf("append: %v", err)
	}
	v, err := l.Verify("unknown")
	if err != nil {
		t.Fatalf("verify: %v", err)
	}
	if v.Valid {
		t.Error("a hash that is not in the chain reported valid")
	}
	if v.Reason != "Hash not found" {
		t.Errorf("reason = %q, want \"Hash not found\"", v.Reason)
	}
}

func TestChainLinksEachRecordToItsPredecessor(t *testing.T) {
	l := open(t)
	var hashes []string
	for i := 0; i < 5; i++ {
		e, err := l.Append(map[string]any{"risk_score": i * 10})
		if err != nil {
			t.Fatalf("append %d: %v", i, err)
		}
		hashes = append(hashes, e.Hash)
	}
	entries, err := l.Entries()
	if err != nil {
		t.Fatalf("entries: %v", err)
	}
	if len(entries) != 5 {
		t.Fatalf("got %d entries, want 5", len(entries))
	}
	if entries[0].Previous != GenesisPrev {
		t.Errorf("first record previous = %s, want genesis", entries[0].Previous)
	}
	for i := 1; i < len(entries); i++ {
		if entries[i].Previous != hashes[i-1] {
			t.Errorf("record %d links to %s, want %s", i, entries[i].Previous, hashes[i-1])
		}
	}
	v, err := l.Verify("")
	if err != nil || !v.Valid || v.Checked != 5 {
		t.Errorf("whole-chain verify = %+v (%v), want valid over 5 records", v, err)
	}
}

// CLAUDE.md invariant 9: signed at origin, never a bare content hash written by the store.
func TestRecordsAreSignedAtOrigin(t *testing.T) {
	l := open(t)
	e, err := l.Append(map[string]any{"risk_score": 42})
	if err != nil {
		t.Fatalf("append: %v", err)
	}
	sig, ok := e.Fields["origin_signature"].(string)
	if !ok || !strings.HasPrefix(sig, "hmac-sha256:") {
		t.Fatalf("origin_signature = %v, want an hmac-sha256 signature", e.Fields["origin_signature"])
	}
	if sig == "hmac-sha256:"+e.Hash {
		t.Error("the signature is just the content hash; that is the thing invariant 9 forbids")
	}
}

// A ledger written by the Python build used the same v2 chain order, so reopening it here must
// verify rather than read as tampering.
func TestLegacyDigestIsDistinctFromV2(t *testing.T) {
	if Digest("a", "b") == LegacyDigest("a", "b") {
		t.Fatal("the two chain orders collide; the version marker would be meaningless")
	}
	if Digest("a", "b") != LegacyDigest("b", "a") {
		t.Error("v2 hashes payload then previous; legacy hashes previous then payload")
	}
}
