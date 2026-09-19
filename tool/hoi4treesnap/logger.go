package main

import (
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const (
	diagnosticDateLayout = "02-01-2006"
	diagnosticTimeLayout = "03-04-05"
)

// initLogger opens (or creates) the application log file under
// <output base>/logs/hoi4treesnap.log and installs it as the default slog
// handler. The returned close function must be called on exit.
// On failure the default slog handler (stderr text) is kept and a warning is
// printed, so the application always continues to run.
func initLogger() func() {
	logDir, err := getLogsOutputDir()
	if err != nil {
		fmt.Fprintf(os.Stderr, "log: could not determine logs dir: %v\n", err)
		return func() {}
	}
	logsPath = logDir

	logPath := filepath.Join(logDir, "hoi4treesnap.log")
	appLogPath = logPath
	f, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o644)
	if err != nil {
		fmt.Fprintf(os.Stderr, "log: could not open log file: %v\n", err)
		return func() {}
	}

	h := slog.NewTextHandler(f, &slog.HandlerOptions{Level: slog.LevelDebug})
	slog.SetDefault(slog.New(h))

	fmt.Fprintf(os.Stderr, "log: %s\n", logPath)
	return func() { _ = f.Close() }
}

func writeDiagnosticLog(fileName, content string) (string, error) {
	return writeDiagnosticLogAt(time.Now(), fileName, content)
}

func writeDiagnosticLogAt(now time.Time, fileName, content string) (string, error) {
	logDir, err := getLogsOutputDir()
	if err != nil {
		return "", err
	}
	logsPath = logDir

	dayDir := filepath.Join(logDir, now.Format(diagnosticDateLayout))
	if err := os.MkdirAll(dayDir, 0o755); err != nil {
		return "", err
	}

	path, err := uniqueDiagnosticLogPath(dayDir, now, fileName)
	if err != nil {
		return "", err
	}

	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o644)
	if err != nil {
		return "", err
	}
	defer f.Close()

	_, err = fmt.Fprintf(f, "[%s]\n%s\n\n", now.Format(time.RFC3339), content)
	if err != nil {
		return "", err
	}

	return path, nil
}

func uniqueDiagnosticLogPath(dayDir string, now time.Time, fileName string) (string, error) {
	base := filepath.Base(fileName)
	ext := filepath.Ext(base)
	name := strings.TrimSuffix(base, ext)
	prefix := fmt.Sprintf("%s-%s-%s", now.Format(diagnosticTimeLayout), now.Format(diagnosticDateLayout), name)
	path := filepath.Join(dayDir, prefix+ext)

	if _, err := os.Stat(path); os.IsNotExist(err) {
		return path, nil
	} else if err != nil {
		return "", err
	}

	for i := 1; ; i++ {
		candidate := filepath.Join(dayDir, fmt.Sprintf("%s-%02d%s", prefix, i, ext))
		if _, err := os.Stat(candidate); os.IsNotExist(err) {
			return candidate, nil
		} else if err != nil {
			return "", err
		}
	}
}

func removeDiagnosticLog(fileName string) error {
	logDir, err := getLogsOutputDir()
	if err != nil {
		return err
	}
	path := filepath.Join(logDir, fileName)
	if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}
