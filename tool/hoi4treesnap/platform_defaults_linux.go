//go:build linux

package main

import (
	"os"
	"path/filepath"
)

func defaultGamePathHint() string {
	homeDir, err := os.UserHomeDir()
	if err != nil {
		return "~/.steam/steam/steamapps/common/Hearts of Iron IV"
	}
	return filepath.Join(homeDir, ".steam", "steam", "steamapps", "common", "Hearts of Iron IV")
}

func autodetectGamePathCandidates() []string {
	homeDir, err := os.UserHomeDir()
	if err != nil {
		return nil
	}

	return []string{
		filepath.Join(homeDir, ".steam", "steam", "steamapps", "common", "Hearts of Iron IV"),
		filepath.Join(homeDir, ".var", "app", "com.valvesoftware.Steam", "data", "Steam", "steamapps", "common", "Hearts of Iron IV"),
	}
}