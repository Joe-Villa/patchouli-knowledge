package main

import (
	"os"
	"path/filepath"
	"strings"
)

const cacheDirName = "hoi4treesnap"

func gamePathCacheFile() (string, error) {
	cacheRoot, err := os.UserCacheDir()
	if err != nil {
		return "", err
	}

	cacheDir := filepath.Join(cacheRoot, cacheDirName)
	if err := os.MkdirAll(cacheDir, 0o755); err != nil {
		return "", err
	}

	return filepath.Join(cacheDir, "hoi4treesnapGamePath.txt"), nil
}

func saveGamePathToCache(path string) error {
	cacheFile, err := gamePathCacheFile()
	if err != nil {
		return err
	}
	return encodeCacheFile(path, cacheFile)
}

func loadInitialGamePath() (string, error) {
	for _, candidate := range autodetectGamePathCandidates() {
		if isExistingDir(candidate) {
			return candidate, nil
		}
	}

	cacheFile, err := gamePathCacheFile()
	if err != nil {
		return "", err
	}

	if _, err := os.Stat(cacheFile); err != nil {
		if os.IsNotExist(err) {
			return "", nil
		}
		return "", err
	}

	var cachedPath string
	if err := decodeCacheFile(&cachedPath, cacheFile); err != nil {
		return "", err
	}
	if !isExistingDir(cachedPath) {
		return "", nil
	}

	return cachedPath, nil
}

func isExistingDir(path string) bool {
	if path == "" {
		return false
	}
	info, err := os.Stat(path)
	if err != nil {
		return false
	}
	return info.IsDir()
}

func outputBaseDir() string {
	if appImagePath := strings.TrimSpace(os.Getenv("APPIMAGE")); appImagePath != "" {
		return filepath.Dir(appImagePath)
	}
	if strings.TrimSpace(binPath) != "" {
		return binPath
	}
	wd, err := os.Getwd()
	if err != nil {
		return "."
	}
	return wd
}

func getLogsOutputDir() (string, error) {
	dir := filepath.Join(outputBaseDir(), "logs")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	return dir, nil
}

func getConfigOutputDir() (string, error) {
	dir := filepath.Join(outputBaseDir(), "config")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	return dir, nil
}