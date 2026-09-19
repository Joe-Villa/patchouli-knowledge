package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
)

const settingsFileName = "settings.json"

type appSettings struct {
	ThemeName            string `json:"theme_name"`
	CustomBackgroundPath string `json:"custom_background_path"`
}

func appSettingsFilePath() (string, error) {
	configDir, err := getConfigOutputDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(configDir, settingsFileName), nil
}

func loadAppSettings() (appSettings, error) {
	path, err := appSettingsFilePath()
	if err != nil {
		return appSettings{}, err
	}

	if _, err := os.Stat(path); err != nil {
		if os.IsNotExist(err) {
			return appSettings{}, nil
		}
		return appSettings{}, err
	}

	b, err := os.ReadFile(path)
	if err != nil {
		return appSettings{}, err
	}

	var s appSettings
	if err := json.Unmarshal(b, &s); err != nil {
		return appSettings{}, err
	}
	return s, nil
}

func saveAppSettings(s appSettings) error {
	path, err := appSettingsFilePath()
	if err != nil {
		return err
	}

	if strings.TrimSpace(s.ThemeName) == "" {
		s.ThemeName = defaultThemeName
	}

	b, err := json.MarshalIndent(s, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, b, 0o644)
}
