package main

import (
	"bufio"
	"context"
	"encoding/json"
	"encoding/gob"
	"errors"
	"fmt"
	"image"
	"image/png"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"

	"github.com/k0kubun/go-ansi"
	"github.com/macroblock/imed/pkg/ptool"
)

const (
	workerEnvEnabled              = "HOI4TREESNAP_WORKER"
	workerEnvAction               = "HOI4TREESNAP_WORKER_ACTION"
	workerEnvFocusFile            = "HOI4TREESNAP_FOCUS_FILE"
	workerEnvGamePath             = "HOI4TREESNAP_GAME_PATH"
	workerEnvModPaths             = "HOI4TREESNAP_MOD_PATHS"
	workerEnvLanguage             = "HOI4TREESNAP_LANGUAGE"
	workerEnvDisableLineRendering = "HOI4TREESNAP_DISABLE_LINE_RENDERING"
	workerEnvRenderBackground     = "HOI4TREESNAP_RENDER_BACKGROUND"
	workerEnvSharedContextFile    = "HOI4TREESNAP_SHARED_CONTEXT_FILE"

	workerExitMalformed = 10
	workerExitFatal     = 11

	workerProgressPrefix = "__HOI4TREESNAP_PROGRESS__="
	workerScanPrefix     = "__HOI4TREESNAP_SCAN__="

	workerActionRender    = "render"
	workerActionScanFocus = "scan-focus"
)

type malformedFocusError struct {
	Path string
	Err  error
}

func (e *malformedFocusError) Error() string {
	return fmt.Sprintf("Skipping malformed focus file %q: %v", e.Path, e.Err)
}

func (e *malformedFocusError) Unwrap() error {
	return e.Err
}

type sharedSpriteType struct {
	Name        string
	TextureFile string
	NoOfFrames  int
}

type sharedRenderContext struct {
	GUI           FocusGUI
	GFX           map[string]sharedSpriteType
	Fonts         map[string]BitmapFont
	Localisations map[string]Localisation
}

type focusWorkerResult struct {
	path      string
	malformed string
	err       error
}

type focusScanPayload struct {
	ModPath string   `json:"mod_path"`
	LocKeys []string `json:"loc_keys"`
	GfxKeys []string `json:"gfx_keys"`
}

type workerProgressPayload struct {
	Value float64 `json:"value"`
	Task  string  `json:"task,omitempty"`
}

type focusScanResult struct {
	path      string
	modPath   string
	locKeys   []string
	gfxKeys   []string
	malformed string
	err       error
}

var ErrGenerationCanceled = errors.New("generation canceled")

func maybeRunWorkerMode() bool {
	if os.Getenv(workerEnvEnabled) != "1" {
		return false
	}

	if err := runWorkerMode(); err != nil {
		var malformed *malformedFocusError
		if errors.As(err, &malformed) {
			fmt.Fprintln(os.Stderr, malformed.Error())
			os.Exit(workerExitMalformed)
		}
		fmt.Fprintln(os.Stderr, err.Error())
		os.Exit(workerExitFatal)
	}
	os.Exit(0)
	return true
}

func runWorkerMode() error {
	gamePath = os.Getenv(workerEnvGamePath)
	language = os.Getenv(workerEnvLanguage)
	isLineRenderingOff = parseWorkerBoolEnv(workerEnvDisableLineRendering)
	isBackgroundRenderingOn = parseWorkerBoolEnv(workerEnvRenderBackground)

	action := strings.TrimSpace(os.Getenv(workerEnvAction))
	if action == "" {
		action = workerActionRender
	}

	switch action {
	case workerActionRender:
		return runWorkerRenderMode()
	case workerActionScanFocus:
		return runWorkerScanFocusMode()
	default:
		return fmt.Errorf("unknown worker action %q", action)
	}
}

func runWorkerRenderMode() error {

	sharedContextPath := os.Getenv(workerEnvSharedContextFile)
	if sharedContextPath == "" {
		return errors.New("worker shared context file not provided")
	}
	shared, err := loadSharedRenderContext(sharedContextPath)
	if err != nil {
		return err
	}

	baseModPaths := splitWorkerListEnv(workerEnvModPaths)
	focusTreePath := os.Getenv(workerEnvFocusFile)
	if focusTreePath == "" {
		return errors.New("worker focus file not provided")
	}

	_, err = renderSelectedFocusTree(context.Background(), focusTreePath, baseModPaths, shared, false, func(v float64, task string) {
		payload := workerProgressPayload{Value: clampUnit(v), Task: task}
		raw, marshalErr := json.Marshal(payload)
		if marshalErr != nil {
			fmt.Printf("%s%.6f\n", workerProgressPrefix, clampUnit(v))
			return
		}
		fmt.Printf("%s%s\n", workerProgressPrefix, string(raw))
	})
	return err
}

func runWorkerScanFocusMode() error {
	if err := ensureParser(); err != nil {
		return err
	}

	focusTreePath := os.Getenv(workerEnvFocusFile)
	if focusTreePath == "" {
		return errors.New("worker focus file not provided")
	}

	clearFocusParseState()
	if err := parseFocus(focusTreePath); err != nil {
		return &malformedFocusError{Path: focusTreePath, Err: err}
	}

	payload := focusScanPayload{
		ModPath: focusFileModPath(focusTreePath),
		LocKeys: append([]string(nil), locList...),
		GfxKeys: append([]string(nil), gfxList...),
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	fmt.Printf("%s%s\n", workerScanPrefix, string(raw))
	return nil
}

func parseWorkerBoolEnv(name string) bool {
	value := strings.TrimSpace(strings.ToLower(os.Getenv(name)))
	return value == "1" || value == "true" || value == "yes"
}

func splitWorkerListEnv(name string) []string {
	raw := os.Getenv(name)
	if raw == "" {
		return nil
	}
	parts := strings.Split(raw, string(os.PathListSeparator))
	out := make([]string, 0, len(parts))
	for _, part := range parts {
		if part == "" {
			continue
		}
		out = append(out, part)
	}
	return out
}

func renderSelectedFocusTrees(ctx context.Context) (int, []string, error) {
	if err := checkGenerationCancelled(ctx); err != nil {
		return 0, nil, err
	}
	selected := append([]string(nil), focusTreePaths...)
	baseModPaths := append([]string(nil), modPaths...)
	setProgressTask("Preparing shared context")

	shared, validPaths, skippedFocusFiles, err := buildSharedRenderContext(ctx, selected, baseModPaths)
	if err != nil {
		return 0, nil, err
	}
	if len(validPaths) == 0 {
		return 0, skippedFocusFiles, nil
	}

	setProgressValue(0.25)

	if len(validPaths) == 1 {
		parsed, skippedDuringRender, err := renderSelectedFocusTreesSequential(ctx, validPaths, baseModPaths, shared)
		return parsed, append(skippedFocusFiles, skippedDuringRender...), err
	}

	parsed, skippedDuringRender, err := renderSelectedFocusTreesParallel(ctx, validPaths, baseModPaths, shared)
	return parsed, append(skippedFocusFiles, skippedDuringRender...), err
}

func buildSharedRenderContext(ctx context.Context, selected []string, baseModPaths []string) (*sharedRenderContext, []string, []string, error) {
	if err := checkGenerationCancelled(ctx); err != nil {
		return nil, nil, nil, err
	}
	locReq := make(map[string]struct{})
	gfxReq := map[string]struct{}{"GFX_focus_can_start": {}}
	selectedModPaths := make([]string, 0)
	validPaths := make([]string, 0, len(selected))
	skippedFocusFiles := make([]string, 0)

	scanResults, skipped, err := scanFocusRequirementsParallel(ctx, selected)
	if err != nil {
		return nil, nil, nil, err
	}
	skippedFocusFiles = append(skippedFocusFiles, skipped...)

	for _, result := range scanResults {
		validPaths = append(validPaths, result.path)
		if !containsString(selectedModPaths, result.modPath) {
			selectedModPaths = append(selectedModPaths, result.modPath)
		}
		for _, key := range result.locKeys {
			locReq[key] = struct{}{}
		}
		for _, key := range result.gfxKeys {
			gfxReq[key] = struct{}{}
		}
	}

	if len(validPaths) == 0 {
		return nil, nil, skippedFocusFiles, nil
	}

	resetGenerationState()
	if err := ensureParser(); err != nil {
		return nil, nil, nil, err
	}
	locMap[language] = make(map[string]Localisation)
	locList = sortedKeys(locReq)
	gfxList = sortedKeys(gfxReq)

	allModPaths := mergeModPaths(baseModPaths, selectedModPaths)
	if err := checkGenerationCancelled(ctx); err != nil {
		return nil, nil, nil, err
	}
	guiPath := findGUIPath(allModPaths)
	setProgressTask("Parsing GUI from " + filepath.Base(guiPath))
	if err := parseGUI(guiPath); err != nil {
		return nil, nil, nil, err
	}
	setProgressValue(0.12)

	for _, p := range allModPaths {
		if err := checkGenerationCancelled(ctx); err != nil {
			return nil, nil, nil, err
		}
		setProgressTask("Parsing GFX: " + filepath.Base(p))
		if err := parseGFX(p, len(allModPaths)); err != nil {
			return nil, nil, nil, err
		}
	}
	setProgressValue(0.18)

	for _, p := range allModPaths {
		if err := checkGenerationCancelled(ctx); err != nil {
			return nil, nil, nil, err
		}
		setProgressTask("Parsing localisation: " + filepath.Base(p))
		if err := parseLoc(p, len(allModPaths)); err != nil {
			return nil, nil, nil, err
		}
	}
	setProgressTask("Shared context ready")
	setProgressValue(0.25)

	shared := snapshotSharedRenderContext()
	resetGenerationState()
	return shared, validPaths, skippedFocusFiles, nil
}

func scanFocusRequirementsParallel(ctx context.Context, selected []string) ([]focusScanResult, []string, error) {
	if len(selected) == 0 {
		return nil, nil, nil
	}

	exePath, err := os.Executable()
	if err != nil {
		return nil, nil, err
	}

	workerCount := runtime.GOMAXPROCS(0)
	if workerCount < 1 {
		workerCount = 1
	}
	if workerCount > len(selected) {
		workerCount = len(selected)
	}

	jobs := make(chan string)
	results := make(chan focusScanResult, len(selected))
	var wg sync.WaitGroup

	for i := 0; i < workerCount; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for focusTreePath := range jobs {
				results <- runFocusScanWorker(ctx, exePath, focusTreePath)
			}
		}()
	}

	go func() {
		wg.Wait()
		close(results)
	}()

	go func() {
		defer close(jobs)
		for _, focusTreePath := range selected {
			select {
			case <-ctx.Done():
				return
			case jobs <- focusTreePath:
			}
		}
	}()

	valid := make([]focusScanResult, 0, len(selected))
	skipped := make([]string, 0)
	completed := 0
	cancelled := false

	for result := range results {
		if errors.Is(ctx.Err(), context.Canceled) {
			cancelled = true
		}
		completed++
		setProgressTask(fmt.Sprintf("Scanning focus requirements (%d/%d): %s", completed, len(selected), filepath.Base(result.path)))
		setProgressValue(0.1 * float64(completed) / float64(len(selected)))

		if result.malformed != "" {
			skipped = append(skipped, result.malformed)
			ansi.Println("\x1b[31;1m" + result.malformed + "\x1b[0m")
			continue
		}
		if result.err != nil {
			return nil, skipped, result.err
		}
		valid = append(valid, result)
	}
	if cancelled {
		return nil, skipped, ErrGenerationCanceled
	}

	return valid, skipped, nil
}

func renderSelectedFocusTreesSequential(ctx context.Context, selected []string, baseModPaths []string, shared *sharedRenderContext) (int, []string, error) {
	parsedFocusFiles := 0
	skippedFocusFiles := make([]string, 0)
	for i, focusTreePath := range selected {
		if err := checkGenerationCancelled(ctx); err != nil {
			return parsedFocusFiles, skippedFocusFiles, err
		}
		setProgressTask(fmt.Sprintf("Rendering %s (%d/%d)", filepath.Base(focusTreePath), i+1, len(selected)))
		_, err := renderSelectedFocusTree(ctx, focusTreePath, baseModPaths, shared, false, nil)
		if err != nil {
			var malformed *malformedFocusError
			if errors.As(err, &malformed) {
				skippedFocusFiles = append(skippedFocusFiles, malformed.Error())
				slog.Warn("skipping malformed focus file", "path", focusTreePath, "error", malformed.Err.Error())
				ansi.Println("\x1b[31;1m" + malformed.Error() + "\x1b[0m")
				continue
			}
			return parsedFocusFiles, skippedFocusFiles, err
		}
		parsedFocusFiles++
		setProgressValue(0.25 + 0.75*float64(i+1)/float64(len(selected)))
	}
	return parsedFocusFiles, skippedFocusFiles, nil
}

func renderSelectedFocusTreesParallel(ctx context.Context, selected []string, baseModPaths []string, shared *sharedRenderContext) (int, []string, error) {
	workerCount := runtime.GOMAXPROCS(0)
	if workerCount < 1 {
		workerCount = 1
	}
	if workerCount > len(selected) {
		workerCount = len(selected)
	}

	exePath, err := os.Executable()
	if err != nil {
		return 0, nil, err
	}
	sharedPath, err := writeSharedRenderContext(shared)
	if err != nil {
		return 0, nil, err
	}
	defer os.Remove(sharedPath)

	modPathEnv := strings.Join(baseModPaths, string(os.PathListSeparator))
	jobs := make(chan string)
	results := make(chan focusWorkerResult, len(selected))
	treeProgress := make(map[string]float64, len(selected))
	for _, path := range selected {
		treeProgress[path] = 0
	}
	var treeProgressMu sync.Mutex
	var wg sync.WaitGroup

	for i := 0; i < workerCount; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for focusTreePath := range jobs {
				results <- runFocusWorker(ctx, exePath, focusTreePath, modPathEnv, sharedPath, func(v float64, task string) {
					updateParallelProgress(treeProgress, focusTreePath, v, len(selected), &treeProgressMu, task)
				})
			}
		}()
	}

	go func() {
		wg.Wait()
		close(results)
	}()

	go func() {
		defer close(jobs)
		for _, focusTreePath := range selected {
			select {
			case <-ctx.Done():
				return
			case jobs <- focusTreePath:
			}
		}
	}()

	parsedFocusFiles := 0
	skippedFocusFiles := make([]string, 0)
	cancelled := false
	for result := range results {
		if errors.Is(ctx.Err(), context.Canceled) {
			cancelled = true
		}
		updateParallelProgress(treeProgress, result.path, 1, len(selected), &treeProgressMu, "Completed")

		if result.malformed != "" {
			skippedFocusFiles = append(skippedFocusFiles, result.malformed)
			ansi.Println("\x1b[31;1m" + result.malformed + "\x1b[0m")
			continue
		}
		if result.err != nil {
			return parsedFocusFiles, skippedFocusFiles, result.err
		}
		parsedFocusFiles++
	}
	if cancelled {
		return parsedFocusFiles, skippedFocusFiles, ErrGenerationCanceled
	}

	return parsedFocusFiles, skippedFocusFiles, nil
}

func runFocusWorker(ctx context.Context, exePath, focusTreePath, modPathEnv, sharedPath string, onProgress func(float64, string)) focusWorkerResult {
	cmd := exec.CommandContext(ctx, exePath)
	cmd.Env = append(os.Environ(),
		workerEnvEnabled+"=1",
		workerEnvAction+"="+workerActionRender,
		workerEnvFocusFile+"="+focusTreePath,
		workerEnvGamePath+"="+gamePath,
		workerEnvModPaths+"="+modPathEnv,
		workerEnvLanguage+"="+language,
		fmt.Sprintf("%s=%t", workerEnvDisableLineRendering, isLineRenderingOff),
		fmt.Sprintf("%s=%t", workerEnvRenderBackground, isBackgroundRenderingOn),
		workerEnvSharedContextFile+"="+sharedPath,
	)

	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return focusWorkerResult{path: focusTreePath, err: fmt.Errorf("worker stdout pipe setup failed for %q: %w", focusTreePath, err)}
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return focusWorkerResult{path: focusTreePath, err: fmt.Errorf("worker stderr pipe setup failed for %q: %w", focusTreePath, err)}
	}

	if err := cmd.Start(); err != nil {
		return focusWorkerResult{path: focusTreePath, err: fmt.Errorf("worker start failed for %q: %w", focusTreePath, err)}
	}

	var outputMu sync.Mutex
	var outputBuilder strings.Builder
	appendOutput := func(line string) {
		if strings.TrimSpace(line) == "" {
			return
		}
		outputMu.Lock()
		if outputBuilder.Len() > 0 {
			outputBuilder.WriteByte('\n')
		}
		outputBuilder.WriteString(line)
		outputMu.Unlock()
	}

	var streamWG sync.WaitGroup
	streamWG.Add(2)
	go func() {
		defer streamWG.Done()
		consumeWorkerStream(stdout, onProgress, appendOutput)
	}()
	go func() {
		defer streamWG.Done()
		consumeWorkerStream(stderr, nil, appendOutput)
	}()

	err = cmd.Wait()
	streamWG.Wait()
	if errors.Is(ctx.Err(), context.Canceled) {
		return focusWorkerResult{path: focusTreePath, err: ErrGenerationCanceled}
	}
	output := []byte(strings.TrimSpace(outputBuilder.String()))
	if err == nil {
		return focusWorkerResult{path: focusTreePath}
	}

	message := sanitizeWorkerOutputMessage(string(output))
	var exitErr *exec.ExitError
	if errors.As(err, &exitErr) {
		switch exitErr.ExitCode() {
		case workerExitMalformed:
			message = preferMalformedLine(message)
			if message == "" {
				message = fmt.Sprintf("Skipping malformed focus file %q", focusTreePath)
			}
			return focusWorkerResult{path: focusTreePath, malformed: message}
		case workerExitFatal:
			if message == "" {
				message = fmt.Sprintf("worker failed for %q", focusTreePath)
			}
			return focusWorkerResult{path: focusTreePath, err: errors.New(message)}
		}
	}

	if message == "" {
		message = err.Error()
	}
	if errors.Is(err, context.Canceled) {
		return focusWorkerResult{path: focusTreePath, err: ErrGenerationCanceled}
	}
	return focusWorkerResult{path: focusTreePath, err: fmt.Errorf("worker failed for %q: %s", focusTreePath, message)}
}

func runFocusScanWorker(ctx context.Context, exePath, focusTreePath string) focusScanResult {
	cmd := exec.CommandContext(ctx, exePath)
	cmd.Env = append(os.Environ(),
		workerEnvEnabled+"=1",
		workerEnvAction+"="+workerActionScanFocus,
		workerEnvFocusFile+"="+focusTreePath,
		workerEnvGamePath+"="+gamePath,
		workerEnvLanguage+"="+language,
	)

	output, err := cmd.CombinedOutput()
	if errors.Is(ctx.Err(), context.Canceled) {
		return focusScanResult{path: focusTreePath, err: ErrGenerationCanceled}
	}
	if err == nil {
		payload, parseErr := parseScanPayload(output)
		if parseErr != nil {
			return focusScanResult{path: focusTreePath, err: fmt.Errorf("scan worker output parse failed for %q: %w", focusTreePath, parseErr)}
		}
		return focusScanResult{
			path:    focusTreePath,
			modPath: payload.ModPath,
			locKeys: payload.LocKeys,
			gfxKeys: payload.GfxKeys,
		}
	}

	message := sanitizeWorkerOutputMessage(string(output))
	var exitErr *exec.ExitError
	if errors.As(err, &exitErr) {
		switch exitErr.ExitCode() {
		case workerExitMalformed:
			message = preferMalformedLine(message)
			if message == "" {
				message = preferMalformedLine(strings.TrimSpace(string(output)))
			}
			if message == "" {
				message = fmt.Sprintf("Skipping malformed focus file %q", focusTreePath)
			}
			return focusScanResult{path: focusTreePath, malformed: message}
		case workerExitFatal:
			if message == "" {
				message = fmt.Sprintf("scan worker failed for %q", focusTreePath)
			}
			return focusScanResult{path: focusTreePath, err: errors.New(message)}
		}
	}

	if message == "" {
		message = err.Error()
	}
	if errors.Is(err, context.Canceled) {
		return focusScanResult{path: focusTreePath, err: ErrGenerationCanceled}
	}
	return focusScanResult{path: focusTreePath, err: fmt.Errorf("scan worker failed for %q: %s", focusTreePath, message)}
}

func parseScanPayload(output []byte) (focusScanPayload, error) {
	var payload focusScanPayload

	lines := strings.Split(strings.TrimSpace(string(output)), "\n")
	for _, line := range lines {
		if !strings.HasPrefix(line, workerScanPrefix) {
			continue
		}
		raw := strings.TrimPrefix(line, workerScanPrefix)
		if err := json.Unmarshal([]byte(raw), &payload); err != nil {
			return focusScanPayload{}, err
		}
		return payload, nil
	}

	return focusScanPayload{}, errors.New("scan payload marker missing")
}

func renderSelectedFocusTree(ctx context.Context, focusTreePath string, baseModPaths []string, shared *sharedRenderContext, trackDetailedProgress bool, progressReporter func(float64, string)) (string, error) {
	resetGenerationState()
	defer resetGenerationState()
	if err := checkGenerationCancelled(ctx); err != nil {
		return "", err
	}

	if err := ensureParser(); err != nil {
		return "", err
	}
	if shared != nil {
		applySharedRenderContext(shared)
	} else {
		locMap[language] = make(map[string]Localisation)
		gfxList = append(gfxList, "GFX_focus_can_start")
	}

	reportProgress := func(v float64, task string) {
		clamped := clampUnit(v)
		if progressReporter != nil {
			progressReporter(clamped, task)
		}
		if task != "" {
			setProgressTask(task)
		}
		if trackDetailedProgress {
			setProgressValue(clamped)
		}
	}
	addReportedProgress := func(delta float64, current *float64) {
		*current = clampUnit(*current + delta)
		reportProgress(*current, "")
	}

	reportedProgress := 0.0
	reportProgress(reportedProgress, "Starting")

	focusTreeName := filepath.Base(focusTreePath)
	focusTreeName = focusTreeName[0 : len(focusTreeName)-len(filepath.Ext(focusTreeName))]

	renderModPaths := mergeModPaths(baseModPaths, []string{focusFileModPath(focusTreePath)})
	previousModPaths := modPaths
	modPaths = renderModPaths
	defer func() {
		modPaths = previousModPaths
	}()

	ansi.Println("\x1b[33;1m" + "Parsing files:" + "\x1b[0m")
	err := parseFocus(focusTreePath)
	if err != nil {
		return "", &malformedFocusError{Path: focusTreePath, Err: err}
	}
	reportedProgress = 0.3
	reportProgress(reportedProgress, "Parsing focus file")

	ansi.Println("\x1b[33;1m" + "Generating images:" + "\x1b[0m")
	var steps float64 = 6
	useModsTexturesIfPresent()
	if err := checkGenerationCancelled(ctx); err != nil {
		return "", err
	}
	setProgressTask("Resolving textures")
	addReportedProgress(0.7/steps, &reportedProgress)

	fillAbsoluteFocusPositions(true)
	if err := checkGenerationCancelled(ctx); err != nil {
		return "", err
	}
	setProgressTask("Calculating focus positions")
	addReportedProgress(0.7/steps, &reportedProgress)

	fillFocusChildAndParentData()
	if err := checkGenerationCancelled(ctx); err != nil {
		return "", err
	}
	setProgressTask("Building focus graph")
	addReportedProgress(0.7/steps, &reportedProgress)

	moveAbsoluteFocusPositionsToPositiveValues()

	x, y := maxFocusPos(focusMap)
	w := (x+2)*gui.FocusSpacing.X + spacingX + 17
	h := (y+1)*gui.FocusSpacing.Y + spacingY

	img := image.NewRGBA(image.Rectangle{image.ZP, image.Point{w, h}})
	err = applyCanvasBackground(img)
	if err != nil {
		return "", err
	}
	if err := checkGenerationCancelled(ctx); err != nil {
		return "", err
	}
	setProgressTask("Applying background")
	addReportedProgress(0.7/steps, &reportedProgress)

	replaceFontPathsIfNotFound()
	font, err = initFont(gui.Name.Font)
	if err != nil {
		return "", err
	}
	fontTreeTitle, err = initFont(gui.NationalFocusTitle.Font)
	if err != nil {
		return "", err
	}
	if err := checkGenerationCancelled(ctx); err != nil {
		return "", err
	}
	setProgressTask("Loading fonts")
	addReportedProgress(0.7/steps, &reportedProgress)

	if !isLineRenderingOff {
		setProgressTask("Rendering lines")
		err = renderLines(img)
		if err != nil {
			return "", err
		}

		err = renderExclusiveLines(img)
		if err != nil {
			return "", err
		}
	}
	addReportedProgress(0.7/steps, &reportedProgress)

	focusErrMap := make(map[string]bool)
	setProgressTask("Rendering focus icons and labels")
	for _, f := range focusMap {
		if err := checkGenerationCancelled(ctx); err != nil {
			return "", err
		}
		err = renderFocus(img, f.X*gui.FocusSpacing.X+spacingX, f.Y*gui.FocusSpacing.Y+spacingY, f.ID)
		if err != nil {
			focusErrMap[err.Error()] = true
		}
	}

	focusErrMapI := 0
	for errString := range focusErrMap {
		if focusErrMapI == len(focusErrMap)-1 {
			return "", errors.New(errString)
		}
		ansi.Println("\x1b[31;1m" + errString + "\x1b[0m")
		focusErrMapI++
	}

	renderDir, err := getRenderOutputDir()
	if err != nil {
		return "", err
	}
	outPath := filepath.Join(renderDir, focusTreeName+".png")
	out, err := os.Create(outPath)
	if err != nil {
		return "", err
	}
	if err := checkGenerationCancelled(ctx); err != nil {
		_ = out.Close()
		_ = os.Remove(outPath)
		return "", err
	}
	err = png.Encode(out, img)
	closeErr := out.Close()
	if err != nil {
		return "", err
	}
	if closeErr != nil {
		return "", closeErr
	}
	ansi.Println("Image saved at \"" + outPath + "\"")
	reportProgress(1, "Writing output")
	if trackDetailedProgress {
		setProgressValue(0)
	}

	return outPath, nil
}

func checkGenerationCancelled(ctx context.Context) error {
	if ctx == nil {
		return nil
	}
	if errors.Is(ctx.Err(), context.Canceled) {
		return ErrGenerationCanceled
	}
	return nil
}

func getRenderOutputDir() (string, error) {
	outDir := filepath.Join(outputBaseDir(), "Renders")
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return "", err
	}
	return outDir, nil
}

func sanitizeWorkerOutputMessage(output string) string {
	lines := strings.Split(strings.ReplaceAll(output, "\r\n", "\n"), "\n")
	filtered := make([]string, 0, len(lines))
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		if strings.Contains(line, "Gtk-WARNING") || strings.Contains(line, "Theme parsing error") {
			continue
		}
		filtered = append(filtered, line)
	}
	return strings.Join(filtered, "\n")
}

func preferMalformedLine(message string) string {
	lines := strings.Split(strings.ReplaceAll(message, "\r\n", "\n"), "\n")
	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "Skipping malformed focus file") {
			return trimmed
		}
	}
	return strings.TrimSpace(message)
}

func clampUnit(v float64) float64 {
	if v < 0 {
		return 0
	}
	if v > 1 {
		return 1
	}
	return v
}

func updateParallelProgress(treeProgress map[string]float64, treePath string, treeValue float64, totalTrees int, mu *sync.Mutex, task string) {
	if totalTrees <= 0 {
		setProgressValue(1)
		return
	}

	mu.Lock()
	current := treeProgress[treePath]
	next := clampUnit(treeValue)
	if next < current {
		next = current
	}
	treeProgress[treePath] = next

	sum := 0.0
	for _, v := range treeProgress {
		sum += v
	}
	overall := 0.25 + 0.75*(sum/float64(totalTrees))
	mu.Unlock()

	if strings.TrimSpace(task) == "" {
		task = "Rendering"
	}
	setProgressTask(fmt.Sprintf("%s: %s", filepath.Base(treePath), task))

	setProgressValue(overall)
}

func consumeWorkerStream(r io.Reader, onProgress func(float64, string), appendOutput func(string)) {
	scanner := bufio.NewScanner(r)
	buf := make([]byte, 0, 64*1024)
	scanner.Buffer(buf, 1024*1024)

	for scanner.Scan() {
		line := scanner.Text()
		if onProgress != nil && strings.HasPrefix(line, workerProgressPrefix) {
			raw := strings.TrimPrefix(line, workerProgressPrefix)
			var payload workerProgressPayload
			if err := json.Unmarshal([]byte(strings.TrimSpace(raw)), &payload); err == nil {
				onProgress(payload.Value, payload.Task)
				continue
			}
			if v, err := strconv.ParseFloat(strings.TrimSpace(raw), 64); err == nil {
				onProgress(v, "")
				continue
			}
		}
		appendOutput(line)
	}
	if err := scanner.Err(); err != nil {
		appendOutput(err.Error())
	}
}

func ensureParser() error {
	if pdx != nil {
		return nil
	}
	var err error
	pdx, err = ptool.NewBuilder().FromString(pdxRule).Entries("entry").Build()
	return err
}

func focusFileModPath(focusTreePath string) string {
	return filepath.Clean(strings.TrimSuffix(filepath.Dir(focusTreePath), filepath.Join("common", "national_focus")))
}

func mergeModPaths(baseModPaths, selectedModPaths []string) []string {
	merged := make([]string, 0, 1+len(baseModPaths)+len(selectedModPaths))
	appendUnique := func(paths []string) {
		for _, p := range paths {
			if p == "" || containsString(merged, p) {
				continue
			}
			merged = append(merged, p)
		}
	}
	appendUnique([]string{gamePath})
	appendUnique(baseModPaths)
	appendUnique(selectedModPaths)
	return merged
}

func findGUIPath(allModPaths []string) string {
	guiPath := gamePath
	for _, p := range allModPaths {
		if _, err := os.Stat(filepath.Join(p, "interface", "nationalfocusview.gui")); err == nil {
			guiPath = p
		}
	}
	return guiPath
}

func sortedKeys(m map[string]struct{}) []string {
	keys := make([]string, 0, len(m))
	for key := range m {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func snapshotSharedRenderContext() *sharedRenderContext {
	shared := &sharedRenderContext{
		GUI:           gui,
		GFX:           make(map[string]sharedSpriteType, len(gfxMap)),
		Fonts:         make(map[string]BitmapFont, len(fontMap)),
		Localisations: make(map[string]Localisation, len(locMap[language])),
	}
	for name, sprite := range gfxMap {
		shared.GFX[name] = sharedSpriteType{
			Name:        sprite.Name,
			TextureFile: sprite.TextureFile,
			NoOfFrames:  sprite.NoOfFrames,
		}
	}
	for name, fontBitmap := range fontMap {
		fontCopy := fontBitmap
		fontCopy.Fontfiles = append([]string(nil), fontBitmap.Fontfiles...)
		shared.Fonts[name] = fontCopy
	}
	for key, loc := range locMap[language] {
		shared.Localisations[key] = loc
	}
	return shared
}

func applySharedRenderContext(shared *sharedRenderContext) {
	gui = shared.GUI
	gfxMap = make(map[string]SpriteType, len(shared.GFX))
	for name, sprite := range shared.GFX {
		gfxMap[name] = SpriteType{
			Name:        sprite.Name,
			TextureFile: sprite.TextureFile,
			NoOfFrames:  sprite.NoOfFrames,
		}
	}
	fontMap = make(map[string]BitmapFont, len(shared.Fonts))
	for name, fontBitmap := range shared.Fonts {
		fontCopy := fontBitmap
		fontCopy.Fontfiles = append([]string(nil), fontBitmap.Fontfiles...)
		fontMap[name] = fontCopy
	}
	locMap = make(map[string]map[string]Localisation)
	locMap[language] = make(map[string]Localisation, len(shared.Localisations))
	for key, loc := range shared.Localisations {
		locMap[language][key] = loc
	}
}

func writeSharedRenderContext(shared *sharedRenderContext) (string, error) {
	file, err := os.CreateTemp("", "hoi4treesnap-shared-*.gob")
	if err != nil {
		return "", err
	}
	defer file.Close()
	if err := gob.NewEncoder(file).Encode(shared); err != nil {
		_ = os.Remove(file.Name())
		return "", err
	}
	return file.Name(), nil
}

func loadSharedRenderContext(path string) (*sharedRenderContext, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	var shared sharedRenderContext
	if err := gob.NewDecoder(file).Decode(&shared); err != nil {
		return nil, err
	}
	return &shared, nil
}

func clearFocusParseState() {
	focusMap = make(map[string]Focus)
	locList = nil
	gfxList = nil
	UDdash = nil
	ULdash = nil
	URdash = nil
	DLdash = nil
	DRdash = nil
	LRdash = nil
	UDLdash = nil
	UDRdash = nil
	ULRdash = nil
	DLRdash = nil
	UDLRdash = nil
	UD = nil
	UL = nil
	UR = nil
	DL = nil
	DR = nil
	LR = nil
	UDL = nil
	UDR = nil
	ULR = nil
	DLR = nil
	UDLR = nil
}

func resetGenerationState() {
	focusMap = make(map[string]Focus)
	gfxMap = make(map[string]SpriteType)
	fontMap = make(map[string]BitmapFont)
	locMap = make(map[string]map[string]Localisation)
	gui = FocusGUI{}
	locList = nil
	gfxList = nil
	UDdash = nil
	ULdash = nil
	URdash = nil
	DLdash = nil
	DRdash = nil
	LRdash = nil
	UDLdash = nil
	UDRdash = nil
	ULRdash = nil
	DLRdash = nil
	UDLRdash = nil
	UD = nil
	UL = nil
	UR = nil
	DL = nil
	DR = nil
	LR = nil
	UDL = nil
	UDR = nil
	ULR = nil
	DLR = nil
	UDLR = nil
}
