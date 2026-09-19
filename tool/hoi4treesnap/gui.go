package main

import (
	"bytes"
	"context"
	_ "embed"
	"errors"
	"fmt"
	"image"
	"image/color"
	"image/draw"
	"image/gif"
	"image/jpeg"
	"image/png"

	xdraw "golang.org/x/image/draw"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"runtime/debug"
	"sort"
	"strings"
	"sync"
	"time"

	"fyne.io/fyne/v2"
	"fyne.io/fyne/v2/canvas"
	"fyne.io/fyne/v2/container"
	fynedialog "fyne.io/fyne/v2/dialog"
	"fyne.io/fyne/v2/layout"
	"fyne.io/fyne/v2/widget"
	"github.com/k0kubun/go-ansi"
	sqdialog "github.com/sqweek/dialog"
)

var runMu sync.Mutex
var progressMu sync.Mutex
var progressCurrent float64
var progressTask string
var loadingGifMu sync.Mutex

//go:embed "hoi4 loading.gif"
var loadingGifData []byte

const (
	focusSelectModeFiles  = "Select files"
	focusSelectModeFolder = "Select folder"
)

type focusSelectionEntry struct {
	Display string
	Paths   []string
}

var focusSelectMode = focusSelectModeFiles
var includeFocusSubfolders = true
var focusSelectionEntries []focusSelectionEntry
var focusModeRadio *widget.RadioGroup
var includeSubfoldersCheck *widget.Check
var focusInlineMessageLabel *widget.Label
var focusFilesList *fyne.Container
var focusFilesStatusLabel *widget.Label
var exportFolderLabel *widget.Label
var progressStatusLabel *widget.Label
var progressStatusContainer *fyne.Container
var runStatusContainer *fyne.Container
var loadingGifWidget *canvas.Image
var loadingGifChip *canvas.Rectangle
var loadingGifFrames []image.Image
var loadingGifDelays []time.Duration
var loadingGifStop chan struct{}
var quitButton *widget.Button
var quitButtonRunning *widget.Button
var cancelButton *widget.Button
var quitOnlyContainer *fyne.Container
var quitCancelContainer *fyne.Container
var actionButtonsContainer *fyne.Container
var currentRunCancel context.CancelFunc
var customBackgroundPath string
var selectedThemeName = defaultThemeName

func setupUI(app fyne.App) {
	win = app.NewWindow("TreeSnap")
	pBar = widget.NewProgressBar()
	pBar.Hide()
	focusFilesLabel = widget.NewLabel("Focus files:")
	focusFilesLabel.Wrapping = fyne.TextWrapWord
	focusFilesStatusLabel = widget.NewLabel("none selected")
	focusFilesStatusLabel.Wrapping = fyne.TextWrapWord
	focusFilesList = container.NewVBox()
	focusInlineMessageLabel = widget.NewLabel("")
	focusInlineMessageLabel.Wrapping = fyne.TextWrapWord

	focusModeRadio = widget.NewRadioGroup([]string{focusSelectModeFiles, focusSelectModeFolder}, func(mode string) {
		focusSelectMode = mode
		updateFocusModeControls()
	})
	focusModeRadio.Horizontal = true
	focusModeRadio.SetSelected(focusSelectModeFiles)

	includeSubfoldersCheck = widget.NewCheck("Include subfolders", func(on bool) {
		includeFocusSubfolders = on
	})
	includeSubfoldersCheck.SetChecked(true)
	gamePathLabel = widget.NewLabel("")
	gamePathLabel.Wrapping = fyne.TextWrapWord
	modPathsLabel = widget.NewLabel("Dependency mods: none selected")
	modPathsLabel.Wrapping = fyne.TextWrapWord
	exportFolderLabel = widget.NewLabel("Render export folder: resolving...")
	exportFolderLabel.Wrapping = fyne.TextWrapWord
	progressStatusLabel = widget.NewLabel("Idle (0.0%)")
	progressStatusLabel.Wrapping = fyne.TextWrapWord
	setupLoadingIndicator()
	progressStatusContainer = container.NewBorder(nil, nil, nil, loadingGifContainerObject(), progressStatusLabel)
	runStatusContainer = container.NewVBox()

	quitButton = widget.NewButton("Quit", func() {
		app.Quit()
	})
	quitButtonRunning = widget.NewButton("Quit", func() {
		app.Quit()
	})
	cancelButton = widget.NewButton("Cancel", func() {
		cancelCurrentRun()
	})
	quitOnlyContainer = container.NewVBox(quitButton)
	quitCancelContainer = container.NewGridWithColumns(2, quitButtonRunning, cancelButton)
	actionButtonsContainer = container.NewVBox(quitOnlyContainer)

	if initialGamePath, err := loadInitialGamePath(); err == nil {
		gamePath = initialGamePath
	} else {
		ansi.Println("\x1b[31;1m" + err.Error() + "\x1b[0m")
	}

	if saved, err := loadAppSettings(); err == nil {
		if strings.TrimSpace(saved.CustomBackgroundPath) != "" {
			customBackgroundPath = saved.CustomBackgroundPath
		}
		if strings.TrimSpace(saved.ThemeName) != "" {
			selectedThemeName = saved.ThemeName
		}
		if _, ok := themePresets[selectedThemeName]; !ok {
			selectedThemeName = defaultThemeName
		}
	} else {
		slog.Warn("failed to load app settings", "error", err.Error())
	}

	updateFocusFilesLabel()
	updateFocusModeControls()
	updateGamePathLabel()
	updateModPathsLabel()
	updateExportFolderLabel()
	setProgressTask("Idle")
	applyThemePreset(app, selectedThemeName)

	focusSelectionBlock := container.NewVBox(
		widget.NewButton("Select focus file(s)", func() { selectFocusEntries() }),
		container.NewHBox(widget.NewLabel("Mode:"), focusModeRadio),
		includeSubfoldersCheck,
		widget.NewButton("Settings", func() { openSettingsWindow(app) }),
		focusInlineMessageLabel,
		focusFilesLabel,
		focusFilesStatusLabel,
		focusFilesList,
	)

	mainContent := container.NewVBox(
		focusSelectionBlock,
		widget.NewButton("Select HOI4 folder", func() { selectGameFolder() }),
		gamePathLabel,
		widget.NewButton("Add dependency mod folder(s)", func() { selectModFolder() }),
		modPathsLabel,
		exportFolderLabel,
		widget.NewButton("Select localisation language", func() { selectLocLanguage(app) }),
		widget.NewButton("Generate image", func() { start() }),
		container.NewHBox(
			widget.NewCheck("Disable line rendering", func(on bool) { lineRenderingToggle(on) }),
			widget.NewCheck("Render with background", func(on bool) { backgroundRenderingToggle(on) }),
		),
	)

	footerContent := container.NewVBox(
		runStatusContainer,
		actionButtonsContainer,
	)

	win.SetContent(
		container.NewBorder(nil, footerContent, nil, nil, mainContent),
	)

	win.CenterOnScreen()
	win.ShowAndRun()
}

func setRenderUIActive(active bool) {
	uiDo(func() {
		if runStatusContainer != nil {
			if active {
				runStatusContainer.Objects = []fyne.CanvasObject{pBar, progressStatusContainer}
			} else {
				runStatusContainer.Objects = nil
			}
			runStatusContainer.Refresh()
		}

		if actionButtonsContainer != nil {
			if active {
				actionButtonsContainer.Objects = []fyne.CanvasObject{quitCancelContainer}
			} else {
				actionButtonsContainer.Objects = []fyne.CanvasObject{quitOnlyContainer}
			}
			actionButtonsContainer.Refresh()
		}

		if win != nil && win.Content() != nil {
			win.Content().Refresh()
			syncWindowToContent(!active)
		}
	})
}

func syncWindowToContent(forceShrink bool) {
	if win == nil || win.Content() == nil {
		return
	}

	min := win.Content().MinSize()
	current := win.Canvas().Size()
	targetW := current.Width
	targetH := current.Height

	if targetW < min.Width {
		targetW = min.Width
	}

	if forceShrink {
		targetH = min.Height
	} else if targetH < min.Height {
		targetH = min.Height
	}

	if targetW != current.Width || targetH != current.Height {
		win.Resize(fyne.NewSize(targetW, targetH))
	}
}

func openSettingsWindow(app fyne.App) {
	settingsWin := app.NewWindow("Settings")
	settingsWin.Resize(fyne.NewSize(900, 620))

	defaultPreview := canvas.NewImageFromImage(nil)
	defaultPreview.FillMode = canvas.ImageFillContain
	defaultPreview.SetMinSize(fyne.NewSize(380, 190))

	customPreview := canvas.NewImageFromImage(nil)
	customPreview.FillMode = canvas.ImageFillContain
	customPreview.SetMinSize(fyne.NewSize(380, 190))

	defaultPathLabel := widget.NewLabel("Default background: resolving...")
	defaultPathLabel.Wrapping = fyne.TextWrapWord
	customPathLabel := widget.NewLabel("Custom background: none selected")
	customPathLabel.Wrapping = fyne.TextWrapWord

	refreshPreviews := func() {
		if p, ok := resolveDefaultBackgroundPath(); ok {
			defaultPathLabel.SetText("Default background: " + p)
			if img, err := decodeImageFile(p); err == nil {
				defaultPreview.Image = img
				defaultPreview.Refresh()
			} else {
				defaultPreview.Image = nil
				defaultPreview.Refresh()
			}
		} else {
			defaultPathLabel.SetText("Default background: not found")
			defaultPreview.Image = nil
			defaultPreview.Refresh()
		}

		if strings.TrimSpace(customBackgroundPath) == "" {
			customPathLabel.SetText("Custom background: none selected")
			customPreview.Image = nil
			customPreview.Refresh()
			return
		}

		customPathLabel.SetText("Custom background: " + customBackgroundPath)
		if img, err := decodeImageFile(customBackgroundPath); err == nil {
			customPreview.Image = img
			customPreview.Refresh()
		} else {
			customPreview.Image = nil
			customPreview.Refresh()
		}
	}

	chooseCustomBackground := func() {
		path, err := sqdialog.File().
			Filter("Image and media", "jpg", "jpeg", "png", "bmp", "gif", "webp", "dds", "tga", "webm").
			Load()
		if err != nil {
			if err == sqdialog.ErrCancelled {
				return
			}
			showError(err)
			return
		}
		if strings.TrimSpace(path) == "" {
			return
		}
		if _, err := decodeImageFile(path); err != nil {
			showError(fmt.Errorf("selected file is not a supported image: %w", err))
			return
		}
		customBackgroundPath = path
		if err := saveAppSettings(appSettings{ThemeName: selectedThemeName, CustomBackgroundPath: customBackgroundPath}); err != nil {
			slog.Warn("failed to persist app settings", "error", err.Error())
		}
		refreshPreviews()
	}

	resetBackground := func() {
		customBackgroundPath = ""
		if err := saveAppSettings(appSettings{ThemeName: selectedThemeName, CustomBackgroundPath: customBackgroundPath}); err != nil {
			slog.Warn("failed to persist app settings", "error", err.Error())
		}
		refreshPreviews()
	}

	themeNames := availableThemePresetNames()
	themeSelect := widget.NewSelect(themeNames, func(name string) {
		if strings.TrimSpace(name) == "" {
			return
		}
		selectedThemeName = name
		applyThemePreset(app, name)
		if err := saveAppSettings(appSettings{ThemeName: selectedThemeName, CustomBackgroundPath: customBackgroundPath}); err != nil {
			slog.Warn("failed to persist app settings", "error", err.Error())
		}
	})
	themeSelect.PlaceHolder = "Select theme"
	themeSelect.SetSelected(selectedThemeName)

	defaultCard := widget.NewCard(
		"Default Background Preview",
		"Uses the game path background when available",
		container.NewVBox(defaultPathLabel, defaultPreview),
	)
	customCard := widget.NewCard(
		"Custom Background Preview",
		"Used when Render with background is enabled",
		container.NewVBox(customPathLabel, customPreview),
	)

	backgroundControls := container.NewHBox(
		widget.NewButton("Choose custom background", chooseCustomBackground),
		widget.NewButton("Use default background", resetBackground),
	)

	themeSection := container.NewVBox(
		widget.NewLabel("Global Theme"),
		themeSelect,
		widget.NewLabel(fmt.Sprintf("Included presets: %d HOI4 palettes", len(themeNames))),
	)

	content := container.NewBorder(
		container.NewVBox(widget.NewLabel("Settings"), widget.NewSeparator()),
		container.NewHBox(layout.NewSpacer(), widget.NewButton("Close", func() { settingsWin.Close() })),
		nil,
		nil,
		container.NewVScroll(container.NewVBox(
			container.NewGridWithColumns(2, defaultCard, customCard),
			backgroundControls,
			widget.NewSeparator(),
			themeSection,
		)),
	)

	settingsWin.SetContent(content)
	refreshPreviews()
	settingsWin.Show()
}

func updateFocusModeControls() {
	if includeSubfoldersCheck == nil {
		return
	}
	if focusSelectMode == focusSelectModeFolder {
		includeSubfoldersCheck.Enable()
		return
	}
	includeSubfoldersCheck.Disable()
}

func selectFocusEntries() {
	clearFocusInlineMessage()
	if focusSelectMode == focusSelectModeFolder {
		selectFocusFolder()
		return
	}
	selectFocusFiles()
}

func selectFocusFiles() {
	openFocusFileDialog(nil)
}

func openFocusFileDialog(selected []string) {
	path, err := sqdialog.File().Filter("Text files", "txt").Load()
	if err != nil {
		if err == sqdialog.ErrCancelled {
			commitSelectedFocusFiles(selected)
			return
		}
		ansi.Println("\x1b[31;1m" + err.Error() + "\x1b[0m")
		showError(err)
		return
	}

	if path == "" {
		commitSelectedFocusFiles(selected)
		return
	}

	if strings.EqualFold(filepath.Ext(path), ".txt") && !containsString(selected, path) {
		selected = append(selected, path)
	}

	confirm := fynedialog.NewConfirm("Add another focus file?", "Select another .txt focus tree file?", func(addAnother bool) {
		if addAnother {
			openFocusFileDialog(selected)
			return
		}
		commitSelectedFocusFiles(selected)
	}, win)
	confirm.Show()
}

func commitSelectedFocusFiles(selected []string) {
	if len(selected) == 0 {
		return
	}
	sort.Strings(selected)
	for _, path := range selected {
		addFocusSelectionEntry(focusSelectionEntry{
			Display: path,
			Paths:   []string{path},
		})
	}
}

func selectFocusFolder() {
	folderPath, err := sqdialog.Directory().Browse()
	if err != nil {
		if err == sqdialog.ErrCancelled {
			return
		}
		ansi.Println("\x1b[31;1m" + err.Error() + "\x1b[0m")
		showError(err)
		return
	}

	if folderPath == "" {
		return
	}

	matched, err := findFocusTreeFiles(folderPath, includeFocusSubfolders)
	if err != nil {
		ansi.Println("\x1b[31;1m" + err.Error() + "\x1b[0m")
		showError(err)
		return
	}

	if len(matched) == 0 {
		setFocusInlineMessage("No focus tree files found in selected folder")
		return
	}

	addFocusSelectionEntry(focusSelectionEntry{
		Display: fmt.Sprintf("%s/ (%d files)", folderPath, len(matched)),
		Paths:   matched,
	})
}

func selectGameFolder() {
	directory, err := sqdialog.Directory().Browse()
	if err != nil {
		if err == sqdialog.ErrCancelled {
			return
		}
		ansi.Println("\x1b[31;1m" + err.Error() + "\x1b[0m")
		showError(err)
		return
	}
	if directory == "" {
		return
	}

	gamePath = directory
	updateGamePathLabel()
	ansi.Println("Game folder selected:", gamePath)

	if err := saveGamePathToCache(gamePath); err != nil {
		ansi.Println("\x1b[31;1m" + err.Error() + "\x1b[0m")
		showError(err)
	}
}

func selectModFolder() {
	directory, err := sqdialog.Directory().Browse()
	if err != nil {
		if err == sqdialog.ErrCancelled {
			return
		}
		ansi.Println("\x1b[31;1m" + err.Error() + "\x1b[0m")
		showError(err)
		return
	}
	if directory == "" {
		return
	}

	modPath := directory
	modPaths = append(modPaths, modPath)
	updateModPathsLabel()
	ansi.Println("Mod folder added:", modPath)
}

func setFocusTreePaths(paths []string) {
	focusTreePaths = append([]string(nil), paths...)
	updateFocusFilesLabel()
	ansi.Println("Focus files selected:", focusTreePaths)
}

func updateFocusFilesLabel() {
	if focusFilesLabel == nil || focusFilesStatusLabel == nil || focusFilesList == nil {
		return
	}
	if len(focusTreePaths) == 0 {
		uiDo(func() {
			focusFilesStatusLabel.SetText("none selected")
			focusFilesList.Objects = nil
			focusFilesList.Refresh()
		})
		return
	}

	rows := make([]fyne.CanvasObject, 0, len(focusSelectionEntries))
	for i, entry := range focusSelectionEntries {
		idx := i
		row := container.NewBorder(nil, nil, nil,
			widget.NewButton("×", func() {
				removeFocusSelectionEntry(idx)
			}),
			widget.NewLabel(entry.Display),
		)
		rows = append(rows, row)
	}

	uiDo(func() {
		focusFilesStatusLabel.SetText(fmt.Sprintf("%d selected", len(focusTreePaths)))
		focusFilesList.Objects = rows
		focusFilesList.Refresh()
	})
}

func addFocusSelectionEntry(entry focusSelectionEntry) {
	if len(entry.Paths) == 0 {
		return
	}

	filtered := make([]string, 0, len(entry.Paths))
	for _, p := range entry.Paths {
		if !containsString(filtered, p) {
			filtered = append(filtered, p)
		}
	}
	if len(filtered) == 0 {
		return
	}

	entry.Paths = filtered
	focusSelectionEntries = append(focusSelectionEntries, entry)
	refreshFocusTreePathsFromEntries()
}

func removeFocusSelectionEntry(index int) {
	if index < 0 || index >= len(focusSelectionEntries) {
		return
	}

	focusSelectionEntries = append(focusSelectionEntries[:index], focusSelectionEntries[index+1:]...)
	refreshFocusTreePathsFromEntries()
}

func refreshFocusTreePathsFromEntries() {
	seen := make(map[string]struct{})
	paths := make([]string, 0)
	for _, entry := range focusSelectionEntries {
		for _, p := range entry.Paths {
			if _, exists := seen[p]; exists {
				continue
			}
			seen[p] = struct{}{}
			paths = append(paths, p)
		}
	}
	sort.Strings(paths)
	setFocusTreePaths(paths)
}

func findFocusTreeFiles(root string, recursive bool) ([]string, error) {
	matched := make([]string, 0)

	if recursive {
		err := filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if d.IsDir() {
				return nil
			}
			if !strings.EqualFold(filepath.Ext(d.Name()), ".txt") {
				return nil
			}
			ok, err := isFocusTreeFile(path)
			if err != nil {
				return err
			}
			if ok {
				matched = append(matched, path)
			}
			return nil
		})
		if err != nil {
			return nil, err
		}
		sort.Strings(matched)
		return matched, nil
	}

	entries, err := os.ReadDir(root)
	if err != nil {
		return nil, err
	}
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		if !strings.EqualFold(filepath.Ext(entry.Name()), ".txt") {
			continue
		}
		path := filepath.Join(root, entry.Name())
		ok, err := isFocusTreeFile(path)
		if err != nil {
			return nil, err
		}
		if ok {
			matched = append(matched, path)
		}
	}
	sort.Strings(matched)
	return matched, nil
}

func isFocusTreeFile(path string) (bool, error) {
	content, err := os.ReadFile(path)
	if err != nil {
		return false, err
	}
	text := string(content)
	return strings.Contains(text, "focus_tree") || strings.Contains(text, "focus = {"), nil
}

func setFocusInlineMessage(msg string) {
	if focusInlineMessageLabel == nil {
		return
	}
	uiDo(func() {
		focusInlineMessageLabel.SetText(msg)
	})
}

func clearFocusInlineMessage() {
	setFocusInlineMessage("")
}

func updateGamePathLabel() {
	if gamePathLabel == nil {
		return
	}
	if gamePath == "" {
		uiDo(func() {
			gamePathLabel.SetText("Game folder: not selected. Hint: " + defaultGamePathHint())
		})
		return
	}
	uiDo(func() {
		gamePathLabel.SetText("Game folder: " + gamePath)
	})
}

func updateModPathsLabel() {
	if modPathsLabel == nil {
		return
	}
	if len(modPaths) == 0 {
		uiDo(func() {
			modPathsLabel.SetText("Dependency mods: none selected")
		})
		return
	}
	uiDo(func() {
		modPathsLabel.SetText("Dependency mods: " + strings.Join(modPaths, ", "))
	})
}

func selectLocLanguage(app fyne.App) {
	w := app.NewWindow("Select localisation language")

	w.SetContent(
		container.NewVBox(
			widget.NewRadioGroup([]string{"English", "Brazilian Portuguese", "German", "French", "Spanish", "Polish", "Russian"}, func(s string) { handleLocLanguageChange(s, w) }),
		),
	)

	w.CenterOnScreen()
	w.Show()
}

func handleLocLanguageChange(s string, w fyne.Window) {
	switch s {
	case "English":
		language = "l_english"
	case "Brazilian Portuguese":
		language = "l_braz_por"
	case "German":
		language = "l_german"
	case "French":
		language = "l_french"
	case "Spanish":
		language = "l_spanish"
	case "Polish":
		language = "l_polish"
	case "Russian":
		language = "l_russian"
	}
	ansi.Println("Language selected:", s)
	w.Close()
}

func lineRenderingToggle(on bool) {
	if on {
		isLineRenderingOff = true
	} else {
		isLineRenderingOff = false
	}
}

func backgroundRenderingToggle(on bool) {
	isBackgroundRenderingOn = on
}

func start() {
	ctx := beginRun()
	if ctx == nil {
		return
	}
	showProgressBar()
	go runGeneration(ctx)
}

func runGeneration(ctx context.Context) {
	defer func() {
		if r := recover(); r != nil {
			stack := string(debug.Stack())
			slog.Error("generation panicked", "error", fmt.Sprintf("%v", r), "stack", stack)
			_, _ = writeDiagnosticLog("error.log", fmt.Sprintf("generation panic: %v\n%s", r, stack))
			showError(fmt.Errorf("generation crashed: %v", r))
			ansi.Println(stack)
		}
		endRun()
	}()

	var err error
	locMap[language] = make(map[string]Localisation)
	gfxList = append(gfxList, "GFX_focus_can_start")

	switch {
	case len(focusTreePaths) == 0:
		showError(errors.New("Focus file not selected"))
		return
	case gamePath == "":
		gamePath, err = loadInitialGamePath()
		if err != nil {
			showError(err)
			return
		}
		if gamePath == "" {
			showError(errors.New("Game path not selected"))
			return
		}
		updateGamePathLabel()
	}

	slog.Info("generation started",
		"focus_files", focusTreePaths,
		"game_path", gamePath,
		"mod_paths", modPaths,
		"language", language,
	)

	// Track start time for benchmarking.
	startTime := time.Now()
	if err := removeDiagnosticLog("malformed_focus_files.log"); err != nil {
		slog.Warn("failed to remove legacy malformed focus log", "error", err.Error())
	}
	setProgressTask("Preparing shared context")

	parsedFocusFiles, skippedFocusFiles, err := renderSelectedFocusTrees(ctx)
	if err != nil {
		if errors.Is(err, ErrGenerationCanceled) || errors.Is(err, context.Canceled) {
			slog.Info("generation cancelled by user")
			uiDo(func() {
				fynedialog.ShowInformation("Cancelled", "Generation cancelled.", win)
			})
			return
		}
		showError(err)
		return
	}

	if parsedFocusFiles == 0 {
		details := "No parsable focus files found in selection"
		if len(skippedFocusFiles) > 0 {
			details = skippedFocusFiles[0]
		}
		showError(errors.New(details))
		return
	}
	if len(skippedFocusFiles) > 0 {
		details := strings.Join(skippedFocusFiles, "\n")
		errorLogPath, errLogErr := writeDiagnosticLog("error.log", "Skipped malformed focus files:\n"+details)

		summaryLines := []string{
			fmt.Sprintf("Skipped %d malformed focus file(s).", len(skippedFocusFiles)),
		}
		if errLogErr == nil {
			summaryLines = append(summaryLines, "Details: "+errorLogPath)
		}
		if appLogPath != "" {
			summaryLines = append(summaryLines, "App log: "+appLogPath)
		}
		if errLogErr != nil {
			summaryLines = append(summaryLines, "Could not write one or more diagnostic files; check stderr output.")
		}

		summary := strings.Join(summaryLines, "\n")
		uiDo(func() {
			fynedialog.ShowInformation("Skipped malformed focus files", summary, win)
		})
	}

	// Print out elapsed time.
	elapsedTime := time.Since(startTime)
	setProgressTask("Completed")
	slog.Info("generation complete", "elapsed", elapsedTime.String())
	ansi.Printf("\x1b[30;1m"+"Elapsed time: %s\n\n"+"\x1b[0m", elapsedTime)
}

func uiDo(fn func()) {
	if fyne.CurrentApp() == nil {
		fn()
		return
	}
	fyne.Do(fn)
}

func beginRun() context.Context {
	runMu.Lock()
	defer runMu.Unlock()
	if running {
		return nil
	}
	running = true
	ctx, cancel := context.WithCancel(context.Background())
	currentRunCancel = cancel
	if cancelButton != nil {
		cancelButton.Enable()
	}
	return ctx
}

func endRun() {
	hideProgressBar()
	runMu.Lock()
	currentRunCancel = nil
	running = false
	runMu.Unlock()
}

func cancelCurrentRun() {
	runMu.Lock()
	cancel := currentRunCancel
	active := running
	runMu.Unlock()

	if !active || cancel == nil {
		return
	}

	setProgressTask("Cancelling...")
	uiDo(func() {
		if cancelButton != nil {
			cancelButton.Disable()
		}
	})
	cancel()
}

func showProgressBar() {
	if pBar == nil {
		return
	}
	setRenderUIActive(true)
	uiDo(func() {
		pBar.Show()
		if progressStatusLabel != nil {
			progressStatusLabel.Show()
		}
		if progressStatusContainer != nil {
			progressStatusContainer.Show()
		}
		if cancelButton != nil {
			cancelButton.Enable()
		}
	})
	startLoadingIndicator()
}

func hideProgressBar() {
	if pBar == nil {
		return
	}
	setProgressValue(0)
	setProgressTask("Idle")
	uiDo(func() {
		pBar.Hide()
		if progressStatusLabel != nil {
			progressStatusLabel.Hide()
		}
		if progressStatusContainer != nil {
			progressStatusContainer.Hide()
		}
		if cancelButton != nil {
			cancelButton.Enable()
		}
	})
	stopLoadingIndicator()
	setRenderUIActive(false)
	ensureCompactIdleLayout()
}

func ensureCompactIdleLayout() {
	uiDo(func() {
		if win == nil || win.Content() == nil {
			return
		}
		win.Content().Refresh()
		syncWindowToContent(true)
	})

	// Some Linux WMs apply size/layout updates one frame later.
	// Run a second compact pass after a short delay to reliably remove residual space.
	go func() {
		time.Sleep(45 * time.Millisecond)
		uiDo(func() {
			if win == nil || win.Content() == nil {
				return
			}
			win.Content().Refresh()
			syncWindowToContent(true)
		})
	}()
}

func setProgressValue(v float64) {
	if v < 0 {
		v = 0
	}
	if v > 1 {
		v = 1
	}

	progressMu.Lock()
	progressCurrent = v
	currentTask := progressTask
	progressMu.Unlock()

	if pBar == nil {
		return
	}

	uiDo(func() {
		pBar.SetValue(v)
		if progressStatusLabel != nil {
			progressStatusLabel.SetText(formatProgressStatus(currentTask, v))
		}
	})
}

func setProgressTask(task string) {
	if strings.TrimSpace(task) == "" {
		task = "Working"
	}

	progressMu.Lock()
	progressTask = task
	v := progressCurrent
	progressMu.Unlock()

	if progressStatusLabel == nil {
		return
	}

	uiDo(func() {
		progressStatusLabel.SetText(formatProgressStatus(task, v))
	})
}

func formatProgressStatus(task string, v float64) string {
	if v < 0 {
		v = 0
	}
	if v > 1 {
		v = 1
	}
	return fmt.Sprintf("%s (%.1f%%)", task, v*100)
}

func addProgress(delta float64) {
	progressMu.Lock()
	next := progressCurrent + delta
	progressMu.Unlock()
	setProgressValue(next)
}

func setupLoadingIndicator() {
	loadingGifChip = canvas.NewRectangle(color.NRGBA{R: 0x1A, G: 0x1A, B: 0x1A, A: 0x88})
	loadingGifChip.Hide()

	loadingGifWidget = canvas.NewImageFromImage(nil)
	loadingGifWidget.FillMode = canvas.ImageFillContain
	loadingGifWidget.Hide()

	g, err := gif.DecodeAll(bytes.NewReader(loadingGifData))
	if err != nil {
		slog.Warn("failed to decode embedded loading gif", "error", err.Error())
		return
	}
	if len(g.Image) == 0 {
		slog.Warn("embedded loading gif has no frames")
		return
	}

	loadingGifFrames = make([]image.Image, len(g.Image))
	loadingGifDelays = make([]time.Duration, len(g.Image))
	for i, frame := range g.Image {
		loadingGifFrames[i] = frame
		delay := 100 * time.Millisecond
		if i < len(g.Delay) {
			if parsed := time.Duration(g.Delay[i]) * 10 * time.Millisecond; parsed > 0 {
				delay = parsed
			}
		}
		loadingGifDelays[i] = delay
	}
	loadingGifWidget.Image = loadingGifFrames[0]
	loadingGifWidget.Refresh()
}
func loadingGifContainerObject() fyne.CanvasObject {
	if loadingGifChip == nil {
		loadingGifChip = canvas.NewRectangle(color.NRGBA{R: 0x1A, G: 0x1A, B: 0x1A, A: 0x88})
		loadingGifChip.Hide()
	}
	if loadingGifWidget == nil {
		placeholder := canvas.NewRectangle(color.Transparent)
		placeholder.SetMinSize(fyne.NewSize(48, 32))
		placeholder.Hide()
		return container.NewGridWrap(fyne.NewSize(52, 34), container.NewStack(loadingGifChip, container.NewCenter(placeholder)))
	}
	loadingGifWidget.SetMinSize(fyne.NewSize(42, 26))
	return container.NewGridWrap(fyne.NewSize(52, 34), container.NewStack(loadingGifChip, container.NewCenter(loadingGifWidget)))
}

func startLoadingIndicator() {
	loadingGifMu.Lock()
	if loadingGifWidget == nil || len(loadingGifFrames) == 0 {
		loadingGifMu.Unlock()
		return
	}
	if loadingGifStop != nil {
		loadingGifMu.Unlock()
		return
	}
	stop := make(chan struct{})
	loadingGifStop = stop
	loadingGifMu.Unlock()

	uiDo(func() {
		if loadingGifChip != nil {
			loadingGifChip.Show()
			loadingGifChip.Refresh()
		}
		if loadingGifWidget != nil {
			loadingGifWidget.Image = loadingGifFrames[0]
			loadingGifWidget.Show()
			loadingGifWidget.Refresh()
		}
	})

	go runLoadingIndicator(stop)
}

func stopLoadingIndicator() {
	loadingGifMu.Lock()
	stop := loadingGifStop
	loadingGifStop = nil
	loadingGifMu.Unlock()

	if stop != nil {
		close(stop)
	}

	uiDo(func() {
		if loadingGifChip != nil {
			loadingGifChip.Hide()
			loadingGifChip.Refresh()
		}
		if loadingGifWidget != nil {
			loadingGifWidget.Hide()
			loadingGifWidget.Refresh()
		}
	})
}

func runLoadingIndicator(stop <-chan struct{}) {
	if len(loadingGifFrames) == 0 {
		return
	}
	frame := 0
	for {
		delay := loadingGifDelays[frame]
		if delay <= 0 {
			delay = 100 * time.Millisecond
		}

		select {
		case <-stop:
			return
		case <-time.After(delay):
		}

		frame = (frame + 1) % len(loadingGifFrames)
		next := loadingGifFrames[frame]
		uiDo(func() {
			if loadingGifWidget != nil {
				loadingGifWidget.Image = next
				loadingGifWidget.Refresh()
			}
		})
	}
}

func updateExportFolderLabel() {
	if exportFolderLabel == nil {
		return
	}
	renderDir, err := getRenderOutputDir()
	text := "Render export folder: unavailable"
	if err == nil {
		text = "Render export folder: " + renderDir
	}
	uiDo(func() {
		exportFolderLabel.SetText(text)
	})
}

func applyCanvasBackground(img *image.RGBA) error {
	if !isBackgroundRenderingOn {
		draw.Draw(img, img.Bounds(), &image.Uniform{color.RGBA{0, 0, 0, 0}}, image.ZP, draw.Src)
		return nil
	}

	// Always start with an opaque base, then layer the tile over it.
	// This keeps the final PNG non-transparent even when tile alpha < 255.
	draw.Draw(img, img.Bounds(), &image.Uniform{color.RGBA{24, 25, 28, 255}}, image.ZP, draw.Src)

	tile, err := loadBackgroundTile()
	if err != nil {
		slog.Warn("could not load background tile, using solid fallback", "error", err.Error())
		return nil
	}

	tileBounds := tile.Bounds()
	if tileBounds.Dx() <= 0 || tileBounds.Dy() <= 0 {
		return fmt.Errorf("invalid background tile size %dx%d", tileBounds.Dx(), tileBounds.Dy())
	}

	// Scale the tile to fill the entire canvas.
	xdraw.BiLinear.Scale(img, img.Bounds(), tile, tileBounds, draw.Over, nil)

	return nil
}

func loadBackgroundTile() (image.Image, error) {
	if strings.TrimSpace(customBackgroundPath) != "" {
		f, err := os.Open(customBackgroundPath)
		if err == nil {
			img, decodeErr := decodeCustomBackground(f, customBackgroundPath)
			_ = f.Close()
			if decodeErr == nil && !isFullyTransparent(img) {
				slog.Info("using selected custom background image", "path", customBackgroundPath)
				return img, nil
			}
			if decodeErr != nil {
				slog.Warn("failed to decode selected custom background image", "path", customBackgroundPath, "error", decodeErr.Error())
			}
		}
	}

	defaultGameBackgroundCandidates := []string{}
	if gamePath != "" {
		defaultGameBackgroundCandidates = append(defaultGameBackgroundCandidates,
			filepath.Join(gamePath, "gfx", "interface", "parch_bg_adjustable.dds"),
		)
	}

	for _, candidate := range defaultGameBackgroundCandidates {
		f, err := os.Open(candidate)
		if err != nil {
			continue
		}
		img, _, decodeErr := image.Decode(f)
		_ = f.Close()
		if decodeErr != nil {
			slog.Warn("failed to decode default game background", "path", candidate, "error", decodeErr.Error())
			continue
		}
		if isFullyTransparent(img) {
			slog.Warn("default game background is fully transparent, skipping", "path", candidate)
			continue
		}
		slog.Info("using default game background", "path", candidate)
		return img, nil
	}

	customDirs := []string{".", binPath, filepath.Dir(binPath)}
	candidate, ok := findCustomBackground(customDirs)
	if ok {

		f, err := os.Open(candidate)
		if err != nil {
			slog.Warn("found custom background but failed to open", "path", candidate, "error", err.Error())
		} else {
			img, decodeErr := decodeCustomBackground(f, candidate)
			_ = f.Close()
			if decodeErr != nil {
				slog.Warn("failed to decode custom background image", "path", candidate, "error", decodeErr.Error())
			} else if isFullyTransparent(img) {
				slog.Warn("custom background image is fully transparent, skipping", "path", candidate)
			} else {
				slog.Info("using custom background image", "path", candidate)
				return img, nil
			}
		}
	}
	slog.Info("no usable default game/custom background found, falling back to Steam tiles")

	assetRelPaths := []string{
		filepath.Join("gfx", "interface", "tiles", "tiled_bg.dds"),
		filepath.Join("gfx", "interface", "tiles", "tiled_focus_bg.dds"),
	}

	for _, assetRelPath := range assetRelPaths {
		candidates := []string{
			filepath.Join("Steam files", assetRelPath),
			filepath.Join(binPath, "Steam files", assetRelPath),
			filepath.Join(binPath, "..", "Steam files", assetRelPath),
			filepath.Join(filepath.Dir(binPath), "Steam files", assetRelPath),
		}

		for _, candidate := range candidates {
			f, err := os.Open(candidate)
			if err != nil {
				continue
			}
			img, _, decodeErr := image.Decode(f)
			_ = f.Close()
			if decodeErr != nil {
				slog.Warn("failed to decode background tile", "path", candidate, "error", decodeErr.Error())
				continue
			}
			if isFullyTransparent(img) {
				slog.Warn("background tile is fully transparent, skipping", "path", candidate)
				continue
			}
			slog.Info("using background tile", "path", candidate)
			return img, nil
		}
	}

	return nil, errors.New("could not locate usable custom background image or Steam background tile")
}

func findCustomBackground(dirs []string) (string, bool) {
	preferred := []string{
		"background.jpg",
		"background.jpeg",
		"backround.jpg",
		"backround.jpeg",
	}

	for _, name := range preferred {
		for _, dir := range dirs {
			if p, ok := findFileIgnoreCase(dir, name); ok {
				return p, true
			}
		}
	}

	return "", false
}

func findFileIgnoreCase(dir, wantedLower string) (string, bool) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return "", false
	}

	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		if strings.ToLower(entry.Name()) == wantedLower {
			return filepath.Join(dir, entry.Name()), true
		}
	}

	return "", false
}

func decodeCustomBackground(f *os.File, path string) (image.Image, error) {
	ext := strings.ToLower(filepath.Ext(path))
	switch ext {
	case ".jpg", ".jpeg":
		return jpeg.Decode(f)
	case ".png":
		return png.Decode(f)
	case ".webm":
		return decodeWEBMFirstFrame(path)
	default:
		img, _, err := image.Decode(f)
		return img, err
	}
}

func decodeWEBMFirstFrame(path string) (image.Image, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 12*time.Second)
	defer cancel()

	cmd := exec.CommandContext(
		ctx,
		"ffmpeg",
		"-hide_banner",
		"-loglevel", "error",
		"-i", path,
		"-frames:v", "1",
		"-f", "image2pipe",
		"-vcodec", "png",
		"-",
	)

	var stderr bytes.Buffer
	cmd.Stderr = &stderr

	raw, err := cmd.Output()
	if err != nil {
		if errors.Is(err, exec.ErrNotFound) {
			return nil, errors.New("ffmpeg is required to use .webm backgrounds but was not found in PATH")
		}
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return nil, errors.New("ffmpeg timed out while extracting WEBM first frame")
		}
		message := strings.TrimSpace(stderr.String())
		if message == "" {
			message = err.Error()
		}
		return nil, fmt.Errorf("ffmpeg failed to extract WEBM first frame: %s", message)
	}

	img, _, decodeErr := image.Decode(bytes.NewReader(raw))
	if decodeErr != nil {
		return nil, fmt.Errorf("failed to decode ffmpeg output frame: %w", decodeErr)
	}
	return img, nil
}

func decodeImageFile(path string) (image.Image, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	return decodeCustomBackground(f, path)
}

func resolveDefaultBackgroundPath() (string, bool) {
	if strings.TrimSpace(gamePath) == "" {
		return "", false
	}
	candidate := filepath.Join(gamePath, "gfx", "interface", "parch_bg_adjustable.dds")
	if _, err := os.Stat(candidate); err != nil {
		return "", false
	}
	return candidate, true
}

func isFullyTransparent(img image.Image) bool {
	b := img.Bounds()
	for y := b.Min.Y; y < b.Max.Y; y++ {
		for x := b.Min.X; x < b.Max.X; x++ {
			_, _, _, a := img.At(x, y).RGBA()
			if a != 0 {
				return false
			}
		}
	}
	return true
}
