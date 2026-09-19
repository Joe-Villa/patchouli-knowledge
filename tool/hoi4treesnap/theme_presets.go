package main

import (
	"image/color"
	"sort"

	"fyne.io/fyne/v2"
	"fyne.io/fyne/v2/theme"
)

const defaultThemeName = "HOI4 Steel"

type paletteTheme struct {
	base   fyne.Theme
	colors map[fyne.ThemeColorName]color.Color
}

func (t *paletteTheme) Color(name fyne.ThemeColorName, variant fyne.ThemeVariant) color.Color {
	if c, ok := t.colors[name]; ok {
		return c
	}
	return t.base.Color(name, variant)
}

func (t *paletteTheme) Font(style fyne.TextStyle) fyne.Resource {
	return t.base.Font(style)
}

func (t *paletteTheme) Icon(name fyne.ThemeIconName) fyne.Resource {
	return t.base.Icon(name)
}

func (t *paletteTheme) Size(name fyne.ThemeSizeName) float32 {
	return t.base.Size(name)
}

func availableThemePresetNames() []string {
	names := make([]string, 0, len(themePresets))
	for name := range themePresets {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

func applyThemePreset(app fyne.App, name string) {
	if app == nil {
		return
	}
	if themePreset, ok := themePresets[name]; ok {
		app.Settings().SetTheme(themePreset)
		return
	}
	app.Settings().SetTheme(themePresets[defaultThemeName])
}

func newPaletteTheme(colors map[fyne.ThemeColorName]color.Color) fyne.Theme {
	return &paletteTheme{base: theme.DefaultTheme(), colors: colors}
}

var themePresets = map[string]fyne.Theme{
	"HOI4 Steel": newPaletteTheme(map[fyne.ThemeColorName]color.Color{
		theme.ColorNameBackground:      color.NRGBA{R: 0x2A, G: 0x2A, B: 0x2A, A: 0xFF},
		theme.ColorNameInputBackground: color.NRGBA{R: 0x30, G: 0x30, B: 0x30, A: 0xFF},
		theme.ColorNameButton:          color.NRGBA{R: 0x4A, G: 0x5A, B: 0x6A, A: 0xFF},
		theme.ColorNameHover:           color.NRGBA{R: 0xC0, G: 0xA0, B: 0x60, A: 0x55},
		theme.ColorNamePrimary:         color.NRGBA{R: 0xC8, G: 0xA9, B: 0x6A, A: 0xFF},
		theme.ColorNameFocus:           color.NRGBA{R: 0xD0, G: 0xB0, B: 0x60, A: 0xFF},
		theme.ColorNameDisabled:        color.NRGBA{R: 0x6A, G: 0x6A, B: 0x6A, A: 0xFF},
	}),
	"HOI4 Parchment": newPaletteTheme(map[fyne.ThemeColorName]color.Color{
		theme.ColorNameBackground:      color.NRGBA{R: 0x3A, G: 0x33, B: 0x2C, A: 0xFF},
		theme.ColorNameInputBackground: color.NRGBA{R: 0x8E, G: 0x7A, B: 0x5A, A: 0xFF},
		theme.ColorNameButton:          color.NRGBA{R: 0x8C, G: 0x6A, B: 0x3A, A: 0xFF},
		theme.ColorNameHover:           color.NRGBA{R: 0xC8, G: 0xA9, B: 0x6A, A: 0x66},
		theme.ColorNamePrimary:         color.NRGBA{R: 0xD0, G: 0xB0, B: 0x60, A: 0xFF},
		theme.ColorNameFocus:           color.NRGBA{R: 0xC2, G: 0xB0, B: 0x8A, A: 0xFF},
		theme.ColorNameDisabled:        color.NRGBA{R: 0x6A, G: 0x6A, B: 0x6A, A: 0xFF},
	}),
	"HOI4 Navy": newPaletteTheme(map[fyne.ThemeColorName]color.Color{
		theme.ColorNameBackground:      color.NRGBA{R: 0x2A, G: 0x2A, B: 0x28, A: 0xFF},
		theme.ColorNameInputBackground: color.NRGBA{R: 0x3A, G: 0x3F, B: 0x4A, A: 0xFF},
		theme.ColorNameButton:          color.NRGBA{R: 0x4A, G: 0x6A, B: 0x8A, A: 0xFF},
		theme.ColorNameHover:           color.NRGBA{R: 0xD0, G: 0xB0, B: 0x60, A: 0x55},
		theme.ColorNamePrimary:         color.NRGBA{R: 0xC8, G: 0xA9, B: 0x6A, A: 0xFF},
		theme.ColorNameFocus:           color.NRGBA{R: 0x4A, G: 0x5A, B: 0x6A, A: 0xFF},
		theme.ColorNameDisabled:        color.NRGBA{R: 0x6A, G: 0x6A, B: 0x6A, A: 0xFF},
	}),
	"HOI4 Charcoal": newPaletteTheme(map[fyne.ThemeColorName]color.Color{
		theme.ColorNameBackground:      color.NRGBA{R: 0x30, G: 0x30, B: 0x30, A: 0xFF},
		theme.ColorNameInputBackground: color.NRGBA{R: 0x2A, G: 0x2A, B: 0x2A, A: 0xFF},
		theme.ColorNameButton:          color.NRGBA{R: 0x4A, G: 0x4A, B: 0x4A, A: 0xFF},
		theme.ColorNameHover:           color.NRGBA{R: 0xC8, G: 0xA9, B: 0x6A, A: 0x44},
		theme.ColorNamePrimary:         color.NRGBA{R: 0xC8, G: 0xA9, B: 0x6A, A: 0xFF},
		theme.ColorNameFocus:           color.NRGBA{R: 0x8C, G: 0x6A, B: 0x3A, A: 0xFF},
		theme.ColorNameDisabled:        color.NRGBA{R: 0x6A, G: 0x6A, B: 0x6A, A: 0xFF},
	}),
	"HOI4 Warm Utility": newPaletteTheme(map[fyne.ThemeColorName]color.Color{
		theme.ColorNameBackground:      color.NRGBA{R: 0x3A, G: 0x33, B: 0x2C, A: 0xFF},
		theme.ColorNameInputBackground: color.NRGBA{R: 0x30, G: 0x30, B: 0x30, A: 0xFF},
		theme.ColorNameButton:          color.NRGBA{R: 0x8C, G: 0x6A, B: 0x3A, A: 0xFF},
		theme.ColorNameHover:           color.NRGBA{R: 0xC8, G: 0xA9, B: 0x6A, A: 0x55},
		theme.ColorNamePrimary:         color.NRGBA{R: 0xC8, G: 0xA9, B: 0x6A, A: 0xFF},
		theme.ColorNameFocus:           color.NRGBA{R: 0xD0, G: 0xB0, B: 0x60, A: 0xFF},
		theme.ColorNameDisabled:        color.NRGBA{R: 0x6A, G: 0x6A, B: 0x6A, A: 0xFF},
	}),
	"HOI4 Parchment Light": newPaletteTheme(map[fyne.ThemeColorName]color.Color{
		theme.ColorNameBackground:      color.NRGBA{R: 0xC2, G: 0xB0, B: 0x8A, A: 0xFF},
		theme.ColorNameInputBackground: color.NRGBA{R: 0xBF, G: 0xAE, B: 0x8A, A: 0xFF},
		theme.ColorNameButton:          color.NRGBA{R: 0x8C, G: 0x6A, B: 0x3A, A: 0xFF},
		theme.ColorNameHover:           color.NRGBA{R: 0xD0, G: 0xB0, B: 0x60, A: 0x55},
		theme.ColorNamePrimary:         color.NRGBA{R: 0x8C, G: 0x6A, B: 0x3A, A: 0xFF},
		theme.ColorNameFocus:           color.NRGBA{R: 0xC8, G: 0xA9, B: 0x6A, A: 0xFF},
		theme.ColorNameDisabled:        color.NRGBA{R: 0x6A, G: 0x6A, B: 0x6A, A: 0xFF},
	}),
	"HOI4 Parchment Aged": newPaletteTheme(map[fyne.ThemeColorName]color.Color{
		theme.ColorNameBackground:      color.NRGBA{R: 0xBF, G: 0xAE, B: 0x8A, A: 0xFF},
		theme.ColorNameInputBackground: color.NRGBA{R: 0x8E, G: 0x7A, B: 0x5A, A: 0xFF},
		theme.ColorNameButton:          color.NRGBA{R: 0x8C, G: 0x6A, B: 0x3A, A: 0xFF},
		theme.ColorNameHover:           color.NRGBA{R: 0xD0, G: 0xB0, B: 0x60, A: 0x44},
		theme.ColorNamePrimary:         color.NRGBA{R: 0x4A, G: 0x3F, B: 0x2A, A: 0xFF},
		theme.ColorNameFocus:           color.NRGBA{R: 0xC8, G: 0xA9, B: 0x6A, A: 0xFF},
		theme.ColorNameDisabled:        color.NRGBA{R: 0x6A, G: 0x6A, B: 0x6A, A: 0xFF},
	}),
	"HOI4 Event Parchment": newPaletteTheme(map[fyne.ThemeColorName]color.Color{
		theme.ColorNameBackground:      color.NRGBA{R: 0x8E, G: 0x7A, B: 0x5A, A: 0xFF},
		theme.ColorNameInputBackground: color.NRGBA{R: 0x3A, G: 0x33, B: 0x2C, A: 0xFF},
		theme.ColorNameButton:          color.NRGBA{R: 0x8C, G: 0x6A, B: 0x3A, A: 0xFF},
		theme.ColorNameHover:           color.NRGBA{R: 0xC8, G: 0xA9, B: 0x6A, A: 0x55},
		theme.ColorNamePrimary:         color.NRGBA{R: 0xD0, G: 0xB0, B: 0x60, A: 0xFF},
		theme.ColorNameFocus:           color.NRGBA{R: 0xC2, G: 0xB0, B: 0x8A, A: 0xFF},
		theme.ColorNameDisabled:        color.NRGBA{R: 0x6A, G: 0x6A, B: 0x6A, A: 0xFF},
	}),
	"HOI4 Brassworks": newPaletteTheme(map[fyne.ThemeColorName]color.Color{
		theme.ColorNameBackground:      color.NRGBA{R: 0x2A, G: 0x2A, B: 0x2A, A: 0xFF},
		theme.ColorNameInputBackground: color.NRGBA{R: 0x3A, G: 0x33, B: 0x2C, A: 0xFF},
		theme.ColorNameButton:          color.NRGBA{R: 0x8C, G: 0x6A, B: 0x3A, A: 0xFF},
		theme.ColorNameHover:           color.NRGBA{R: 0xD0, G: 0xB0, B: 0x60, A: 0x55},
		theme.ColorNamePrimary:         color.NRGBA{R: 0xC8, G: 0xA9, B: 0x6A, A: 0xFF},
		theme.ColorNameFocus:           color.NRGBA{R: 0xD0, G: 0xB0, B: 0x60, A: 0xFF},
		theme.ColorNameDisabled:        color.NRGBA{R: 0x6A, G: 0x6A, B: 0x6A, A: 0xFF},
	}),
	"HOI4 Research Blue": newPaletteTheme(map[fyne.ThemeColorName]color.Color{
		theme.ColorNameBackground:      color.NRGBA{R: 0x2A, G: 0x2A, B: 0x2A, A: 0xFF},
		theme.ColorNameInputBackground: color.NRGBA{R: 0x3A, G: 0x3F, B: 0x4A, A: 0xFF},
		theme.ColorNameButton:          color.NRGBA{R: 0x4A, G: 0x6A, B: 0x8A, A: 0xFF},
		theme.ColorNameHover:           color.NRGBA{R: 0xC8, G: 0xA9, B: 0x6A, A: 0x44},
		theme.ColorNamePrimary:         color.NRGBA{R: 0x4A, G: 0x6A, B: 0x8A, A: 0xFF},
		theme.ColorNameFocus:           color.NRGBA{R: 0x4A, G: 0x5A, B: 0x6A, A: 0xFF},
		theme.ColorNameDisabled:        color.NRGBA{R: 0x6A, G: 0x6A, B: 0x6A, A: 0xFF},
	}),
	"HOI4 Naval Steel": newPaletteTheme(map[fyne.ThemeColorName]color.Color{
		theme.ColorNameBackground:      color.NRGBA{R: 0x2A, G: 0x2A, B: 0x28, A: 0xFF},
		theme.ColorNameInputBackground: color.NRGBA{R: 0x3A, G: 0x3F, B: 0x4A, A: 0xFF},
		theme.ColorNameButton:          color.NRGBA{R: 0x4A, G: 0x5A, B: 0x6A, A: 0xFF},
		theme.ColorNameHover:           color.NRGBA{R: 0xC0, G: 0xA0, B: 0x60, A: 0x55},
		theme.ColorNamePrimary:         color.NRGBA{R: 0x4A, G: 0x6A, B: 0x8A, A: 0xFF},
		theme.ColorNameFocus:           color.NRGBA{R: 0xC8, G: 0xA9, B: 0x6A, A: 0xFF},
		theme.ColorNameDisabled:        color.NRGBA{R: 0x6A, G: 0x6A, B: 0x6A, A: 0xFF},
	}),
	"HOI4 Military Olive": newPaletteTheme(map[fyne.ThemeColorName]color.Color{
		theme.ColorNameBackground:      color.NRGBA{R: 0x2A, G: 0x2A, B: 0x2A, A: 0xFF},
		theme.ColorNameInputBackground: color.NRGBA{R: 0x4A, G: 0x4F, B: 0x45, A: 0xFF},
		theme.ColorNameButton:          color.NRGBA{R: 0x6F, G: 0x8A, B: 0x4A, A: 0xFF},
		theme.ColorNameHover:           color.NRGBA{R: 0xD0, G: 0xB0, B: 0x60, A: 0x44},
		theme.ColorNamePrimary:         color.NRGBA{R: 0x6F, G: 0x8A, B: 0x4A, A: 0xFF},
		theme.ColorNameFocus:           color.NRGBA{R: 0xC8, G: 0xA9, B: 0x6A, A: 0xFF},
		theme.ColorNameDisabled:        color.NRGBA{R: 0x6A, G: 0x6A, B: 0x6A, A: 0xFF},
	}),
	"HOI4 Alert Crimson": newPaletteTheme(map[fyne.ThemeColorName]color.Color{
		theme.ColorNameBackground:      color.NRGBA{R: 0x2A, G: 0x2A, B: 0x2A, A: 0xFF},
		theme.ColorNameInputBackground: color.NRGBA{R: 0x30, G: 0x30, B: 0x30, A: 0xFF},
		theme.ColorNameButton:          color.NRGBA{R: 0xA0, G: 0x30, B: 0x30, A: 0xFF},
		theme.ColorNameHover:           color.NRGBA{R: 0xB0, G: 0x40, B: 0x40, A: 0x44},
		theme.ColorNamePrimary:         color.NRGBA{R: 0xB0, G: 0x40, B: 0x40, A: 0xFF},
		theme.ColorNameFocus:           color.NRGBA{R: 0xD0, G: 0xB0, B: 0x60, A: 0xFF},
		theme.ColorNameDisabled:        color.NRGBA{R: 0x6A, G: 0x6A, B: 0x6A, A: 0xFF},
	}),
	"HOI4 Command Red": newPaletteTheme(map[fyne.ThemeColorName]color.Color{
		theme.ColorNameBackground:      color.NRGBA{R: 0x2A, G: 0x2A, B: 0x2A, A: 0xFF},
		theme.ColorNameInputBackground: color.NRGBA{R: 0x3A, G: 0x33, B: 0x2C, A: 0xFF},
		theme.ColorNameButton:          color.NRGBA{R: 0x7A, G: 0x2A, B: 0x2A, A: 0xFF},
		theme.ColorNameHover:           color.NRGBA{R: 0xA0, G: 0x30, B: 0x30, A: 0x44},
		theme.ColorNamePrimary:         color.NRGBA{R: 0xA0, G: 0x30, B: 0x30, A: 0xFF},
		theme.ColorNameFocus:           color.NRGBA{R: 0xC8, G: 0xA9, B: 0x6A, A: 0xFF},
		theme.ColorNameDisabled:        color.NRGBA{R: 0x6A, G: 0x6A, B: 0x6A, A: 0xFF},
	}),
	"HOI4 Portrait Uniform": newPaletteTheme(map[fyne.ThemeColorName]color.Color{
		theme.ColorNameBackground:      color.NRGBA{R: 0x2A, G: 0x2A, B: 0x28, A: 0xFF},
		theme.ColorNameInputBackground: color.NRGBA{R: 0x3A, G: 0x3F, B: 0x4A, A: 0xFF},
		theme.ColorNameButton:          color.NRGBA{R: 0x5A, G: 0x4A, B: 0x3A, A: 0xFF},
		theme.ColorNameHover:           color.NRGBA{R: 0x7A, G: 0x6A, B: 0x4A, A: 0x55},
		theme.ColorNamePrimary:         color.NRGBA{R: 0x7A, G: 0x6A, B: 0x4A, A: 0xFF},
		theme.ColorNameFocus:           color.NRGBA{R: 0x4A, G: 0x4A, B: 0x4A, A: 0xFF},
		theme.ColorNameDisabled:        color.NRGBA{R: 0x6A, G: 0x6A, B: 0x6A, A: 0xFF},
	}),
}
