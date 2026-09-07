// Command voxguard is the VoxGuard / VoiceShield AI gateway.
//
// It serves the dashboard API on :8000 and delegates every audio and ML operation to the Python
// sidecar on :8801. See docs/01-ARCHITECTURE.md section 2 for which component belongs where.
package main

import (
	"context"
	"errors"
	"flag"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/vox-guard/voxguard/gateway/internal/alerts"
	"github.com/vox-guard/voxguard/gateway/internal/decision"
	"github.com/vox-guard/voxguard/gateway/internal/httpapi"
	"github.com/vox-guard/voxguard/gateway/internal/ledger"
	"github.com/vox-guard/voxguard/gateway/internal/policy"
	"github.com/vox-guard/voxguard/gateway/internal/session"
	"github.com/vox-guard/voxguard/gateway/internal/sidecar"
)

func main() {
	addr := flag.String("addr", envOr("VOXGUARD_ADDR", "127.0.0.1:8000"), "gateway listen address")
	sidecarURL := flag.String("sidecar", envOr("VOXGUARD_SIDECAR_URL", "http://127.0.0.1:8801"),
		"base URL of the Python ML sidecar")
	ledgerPath := flag.String("ledger", envOr("LEDGER_PATH", filepath.Join("backend", "data", "ledger.db")),
		"path to the audit ledger database")
	flag.Parse()

	log := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo}))

	// The origin signing key is read from the environment so a deployment can rotate it. The
	// fallback is a demo key and is labelled as one: an audit record signed with a key that is in
	// the repository proves provenance to nobody, and pretending otherwise would be worse than
	// the honest default.
	signingKey := []byte(envOr("VOXGUARD_SIGNING_KEY", "voxguard-demo-origin-signing-key-not-secret"))
	if os.Getenv("VOXGUARD_SIGNING_KEY") == "" {
		log.Warn("using the built-in demo signing key; audit signatures are not attributable",
			"fix", "set VOXGUARD_SIGNING_KEY")
	}

	sc, err := sidecar.New(*sidecarURL, 30*time.Second)
	if err != nil {
		log.Error("sidecar client", "error", err)
		os.Exit(1)
	}

	audit, err := ledger.Open(*ledgerPath, signingKey)
	if err != nil {
		log.Error("open ledger", "error", err, "path", *ledgerPath)
		os.Exit(1)
	}
	defer audit.Close()

	pack := policy.Default
	decisions := decision.New(signingKey)
	alertStore := alerts.NewStore()
	sessions := session.NewManager(sc, decisions, alertStore, audit, pack, func(err error) {
		// docs/01 section 1.5: audit is an interface, not a dependency. A failed write must not
		// stop the call being scored, and must not pass silently either.
		log.Error("audit write failed", "error", err)
	})

	server := &httpapi.Server{
		Sessions: sessions, Decisions: decisions, Alerts: alertStore,
		Ledger: audit, Sidecar: sc, Policy: pack, Log: log,
		AllowedOrigins: []string{"http://localhost:5173", "http://127.0.0.1:5173"},
	}

	httpServer := &http.Server{
		Addr:              *addr,
		Handler:           server.Routes(),
		ReadHeaderTimeout: 10 * time.Second,
		// No WriteTimeout: the dashboard WebSocket is a long-lived stream, and a write deadline
		// would sever it mid-call.
		IdleTimeout: 120 * time.Second,
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	go func() {
		log.Info("gateway listening", "addr", *addr, "sidecar", *sidecarURL,
			"policy_version", pack.Version)
		if err := httpServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Error("listen", "error", err)
			stop()
		}
	}()

	<-ctx.Done()
	log.Info("shutting down")
	sessions.Shutdown()

	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := httpServer.Shutdown(shutdownCtx); err != nil {
		log.Warn("shutdown", "error", err)
	}
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
