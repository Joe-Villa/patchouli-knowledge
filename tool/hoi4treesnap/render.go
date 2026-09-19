package main

import (
	"fmt"
	"image"
	"image/draw"
	"math"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"unicode"
)

func renderFocus(dst draw.Image, x, y int, id string) error {
	f, ok := focusMap[id]
	if !ok {
		return fmt.Errorf("focus id %q not found", id)
	}

	if !f.AllowBranch {
		return nil
	}

	// Original game uses "GFX_technology_unavailable_item_bg" for some reason and replaces it with "GFX_focus_unavailable" via hardcoded part.
	s := gfxMap["GFX_focus_unavailable"]
	if len(f.Prerequisite) == 0 && f.Available {
		s = gfxMap["GFX_focus_can_start"]
	}

	err := renderSprite(dst, x+gui.BG.Position.X, y+gui.BG.Position.Y, gui.BG.Orientation, gui.BG.CenterPosition, s)
	if err != nil {
		return fmt.Errorf("%v: %v", s.TextureFile, err)
	}

	symbol, ok := gfxMap[f.Icon]
	if !ok {
		symbol = gfxMap["GFX_goal_unknown"]
	}

	err = renderSprite(dst, x+gui.Symbol.Position.X, y+gui.Symbol.Position.Y, gui.Symbol.Orientation, gui.Symbol.CenterPosition, symbol)
	if err != nil {
		slog.Warn("failed to render focus icon, trying fallback", "focus_id", f.ID, "icon", f.Icon, "texture", symbol.TextureFile, "error", err.Error())
		fallback, ok := gfxMap["GFX_goal_unknown"]
		if !ok {
			return fmt.Errorf("%v: %v", symbol.TextureFile, err)
		}
		fallbackErr := renderSprite(dst, x+gui.Symbol.Position.X, y+gui.Symbol.Position.Y, gui.Symbol.Orientation, gui.Symbol.CenterPosition, fallback)
		if fallbackErr != nil {
			return fmt.Errorf("%v: %v", fallback.TextureFile, fallbackErr)
		}
	}

	text := f.Text
	if text == "" {
		text = f.ID
	}

	textX := x + gui.Name.Position.X
	textY := y + gui.Name.Position.Y
	if strings.ToLower(gui.Name.Format) == "center" {
		textX += gui.Name.MaxWidth / 2
	}
	if strings.ToLower(gui.Name.VerticalAlignment) == "center" {
		textY += gui.Name.MaxHeight / 2
	}

	displayText := locMap[language][text].Value
	if displayText == "" {
		// Fall back to a humanized localisation key so labels stay readable.
		slog.Debug("no localisation found", "key", text, "id", f.ID, "language", language)
		displayText = humanizeFocusLabel(text)
	} else if looksLikeLocKey(displayText) {
		// Some localisation files contain unresolved/raw keys as values.
		// Normalize those too so the image remains readable.
		displayText = humanizeFocusLabel(displayText)
	}
	font.RenderTextBox(dst, textX, textY, gui.Name.MaxWidth+2, gui.Name.MaxHeight, true, true, displayText)

	return nil
}

func humanizeFocusLabel(key string) string {
	if key == "" {
		return key
	}

	// Remove country-tag style prefixes (e.g. AUS_, MAC_) for readability.
	if idx := strings.IndexByte(key, '_'); idx > 0 {
		prefix := key[:idx]
		if isTagPrefix(prefix) && len(prefix) >= 2 && len(prefix) <= 4 {
			key = key[idx+1:]
		}
	}

	replacer := strings.NewReplacer("_", " ", "-", " ", ".", " ")
	parts := strings.Fields(replacer.Replace(key))
	if len(parts) == 0 {
		return key
	}

	for i, part := range parts {
		runes := []rune(strings.ToLower(part))
		if len(runes) == 0 {
			continue
		}
		runes[0] = unicode.ToUpper(runes[0])
		parts[i] = string(runes)
	}

	return strings.Join(parts, " ")
}

func isTagPrefix(s string) bool {
	if s == "" {
		return false
	}
	hasLetter := false
	for _, r := range s {
		if (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') {
			if (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z') {
				hasLetter = true
			}
			continue
		}
		return false
	}
	return hasLetter
}

func looksLikeLocKey(s string) bool {
	if s == "" {
		return false
	}
	// Human-readable localisation usually contains spaces.
	if strings.ContainsRune(s, ' ') {
		return false
	}
	// Key-like values usually contain separators.
	if !strings.ContainsAny(s, "_-.") {
		return false
	}
	for _, r := range s {
		if (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z') ||
			(r >= '0' && r <= '9') || r == '_' || r == '-' || r == '.' {
			continue
		}
		return false
	}
	return true
}

func renderSprite(dst draw.Image, x, y int, orientation, centerPosition string, sprite SpriteType) error {
	// Read image data.
	err := sprite.readTexture()
	if err != nil {
		return err
	}

	if strings.ToLower(orientation) == "center" {
		x += gui.NationalFocusItem.Width / 2
		y += gui.NationalFocusItem.Height / 2
	}

	if strings.ToLower(centerPosition) == "yes" {
		x -= sprite.Image.Bounds().Max.X / 2
		y -= sprite.Image.Bounds().Max.Y / 2
	}

	draw.Draw(dst, image.Rectangle{image.Point{x, y}, image.Point{x + sprite.Image.Bounds().Max.X, y + sprite.Image.Bounds().Max.Y}}, sprite.Image, image.ZP, draw.Over)

	return nil
}

func renderExclusiveLines(dst *image.RGBA) error {
	for _, f1 := range focusMap {
		if !f1.AllowBranch {
			continue
		}
	OUTER:
		for _, e1 := range f1.MutuallyExclusive {
			f2 := focusMap[e1]
			if !f2.AllowBranch {
				continue
			}

			// Ignore focuses with different Y coordinates, exclusivity links are not drawn in that case.
			// Ignore focuses on the right side of the exclusivity link. We gonna draw from the left ones.
			if (f1.Y != f2.Y) || (f1.X > f2.X) {
				continue
			}

			// Ignore exclusivity links that pass through other focuses.
			for _, e2 := range f1.MutuallyExclusive {
				f3 := focusMap[e2]
				if (f1.Y == f3.Y) && (f2.X > f3.X) && (f1.X < f3.X) {
					continue OUTER
				}
			}

			x := f1.X*gui.FocusSpacing.X + gui.NationalFocusExclusiveItem.Position.X + gui.ExclusiveOffset.X + spacingX
			y := f1.Y*gui.FocusSpacing.Y + gui.NationalFocusExclusiveItem.Position.Y + gui.ExclusiveOffset.Y + spacingY

			// 1x32 if 2 pos difference
			// 4x32 if 3 pos difference
			// 7x32 if 4 pos difference
			xDifference := f2.X - f1.X

			// Just draw mid part if the position difference is only 2.
			if xDifference == 2 {
				// Mid.
				mid := gfxMap[gui.Mid.SpriteType]
				err := mid.readTexture()
				if err != nil {
					return err
				}
				img, err := mid.getFrame(gui.Mid.Frame)
				if err != nil {
					return err
				}
				draw.Draw(dst,
					image.Rectangle{
						image.Point{x, y},
						image.Point{x + img.Bounds().Max.X, y + img.Bounds().Max.Y}},
					img,
					image.ZP,
					draw.Over)

			} else if xDifference > 2 {
				lineSize := (xDifference - 2) * 3 * 32

				// Link1.
				link1 := gfxMap[gui.Link1.SpriteType]
				err := link1.readTexture()
				if err != nil {
					return err
				}
				img, err := link1.getFrame(gui.Link1.Frame)
				if err != nil {
					return err
				}

				for i := 0; i < lineSize/gui.LinkSpacing.X; i++ {
					draw.Draw(dst,
						image.Rectangle{
							image.Point{x + gui.Link1.Position.X + gui.LinkSpacing.X*i, y + gui.Link1.Position.Y - 2},
							image.Point{x + img.Bounds().Max.X + gui.Link1.Position.X + gui.LinkSpacing.X*i, y + img.Bounds().Max.Y + gui.Link1.Position.Y - 2}},
						img,
						image.ZP,
						draw.Over)
				}

				// Left.
				left := gfxMap[gui.Left.SpriteType]
				err = left.readTexture()
				if err != nil {
					return err
				}
				img, err = left.getFrame(gui.Left.Frame)
				if err != nil {
					return err
				}

				draw.Draw(dst,
					image.Rectangle{
						image.Point{x + gui.Right.Position.X, y + gui.Right.Position.Y},
						image.Point{x + img.Bounds().Max.X + gui.Right.Position.X, y + img.Bounds().Max.Y + gui.Right.Position.Y}},
					img,
					image.ZP,
					draw.Over)

				// Right.
				right := gfxMap[gui.Right.SpriteType]
				err = right.readTexture()
				if err != nil {
					return err
				}
				img, err = right.getFrame(gui.Right.Frame)
				if err != nil {
					return err
				}

				draw.Draw(dst,
					image.Rectangle{
						image.Point{x + lineSize + gui.Right.Position.X, y + gui.Right.Position.Y},
						image.Point{x + img.Bounds().Max.X + lineSize + gui.Right.Position.X, y + img.Bounds().Max.Y + gui.Right.Position.Y}},
					img,
					image.ZP,
					draw.Over)

				// Mid.
				mid := gfxMap[gui.Mid.SpriteType]
				err = mid.readTexture()
				if err != nil {
					return err
				}
				img, err = mid.getFrame(gui.Mid.Frame)
				if err != nil {
					return err
				}

				draw.Draw(dst,
					image.Rectangle{
						image.Point{x + lineSize/2 + gui.Right.Position.X, y + gui.Right.Position.Y},
						image.Point{x + img.Bounds().Max.X + lineSize/2 + gui.Right.Position.X, y + img.Bounds().Max.Y + gui.Right.Position.Y}},
					img,
					image.ZP,
					draw.Over)
			}
		}
	}
	return nil
}

func renderLines(dst *image.RGBA) error {
	var err error
	// Load the textures — each call must be checked individually; sharing err
	// across assignments silently discards all but the last error.
	if UD, UDdash, err = readTextureAndGetFrames("GFX_focus_link_up_down", 3, 4); err != nil {
		return err
	}
	if UL, ULdash, err = readTextureAndGetFrames("GFX_focus_link_up_left", 3, 4); err != nil {
		return err
	}
	if UR, URdash, err = readTextureAndGetFrames("GFX_focus_link_up_right", 3, 4); err != nil {
		return err
	}
	if DL, DLdash, err = readTextureAndGetFrames("GFX_focus_link_down_left", 3, 4); err != nil {
		return err
	}
	if DR, DRdash, err = readTextureAndGetFrames("GFX_focus_link_down_right", 3, 4); err != nil {
		return err
	}
	if LR, LRdash, err = readTextureAndGetFrames("GFX_focus_link_left_right", 3, 4); err != nil {
		return err
	}
	if UDL, UDLdash, err = readTextureAndGetFrames("GFX_focus_link_up_down_left", 3, 4); err != nil {
		return err
	}
	if UDR, UDRdash, err = readTextureAndGetFrames("GFX_focus_link_up_down_right", 3, 4); err != nil {
		return err
	}
	if ULR, ULRdash, err = readTextureAndGetFrames("GFX_focus_link_up_left_right", 3, 4); err != nil {
		return err
	}
	if DLR, DLRdash, err = readTextureAndGetFrames("GFX_focus_link_down_left_right", 3, 4); err != nil {
		return err
	}
	if UDLR, UDLRdash, err = readTextureAndGetFrames("GFX_focus_link_up_down_left_right", 3, 4); err != nil {
		return err
	}

	var drawnCoords []image.Point
	for _, p := range focusMap {
		if len(p.Children) > 0 && p.AllowBranch {
			x := p.X*gui.FocusSpacing.X + gui.NationalFocusLink.Position.X + gui.LinkBegin.X + gui.LinkOffsets.X + spacingX
			y := p.Y*gui.FocusSpacing.Y + gui.NationalFocusLink.Position.Y + gui.LinkBegin.Y + gui.LinkOffsets.Y + spacingY - 16

			// First link (out).
			img := UD
			if p.Out.Dir < 16 {
				img = UDdash
			}
			draw.Draw(dst,
				image.Rectangle{
					image.Point{x, y},
					image.Point{x + img.Bounds().Max.X, y + img.Bounds().Max.Y}},
				img,
				image.ZP,
				draw.Over)

			y += UD.Bounds().Max.Y

			// First corner (out).
			img = p.Out.Get()
			if img != nil {
				draw.Draw(dst,
					image.Rectangle{
						image.Point{x, y},
						image.Point{x + img.Bounds().Max.X, y + img.Bounds().Max.Y}},
					img,
					image.ZP,
					draw.Over)
			}
			drawnCoords = append(drawnCoords, image.Point{x, y})

			cornerXvalues := []int{x}
			for _, c := range p.Children {
				c := focusMap[c.ID]
				if c.AllowBranch {
					cornerXvalues = append(cornerXvalues, c.X*gui.FocusSpacing.X+gui.NationalFocusLink.Position.X+gui.LinkBegin.X+gui.LinkOffsets.X+spacingX)
				}
			}

			var isPrevSolid bool
			for _, c := range p.Children {
				c := focusMap[c.ID]
				if !c.AllowBranch {
					continue
				}

				x := c.X*gui.FocusSpacing.X + gui.NationalFocusLink.Position.X + gui.LinkEnd.X + gui.LinkOffsets.X + spacingX

				// Children horizontal lines.
				if c.X != p.X {
					step := gui.LinkSpacing.X
					if c.X > p.X {
						step = -gui.LinkSpacing.X
						isPrevSolid = false
						for _, c2 := range p.Children {
							c2 := focusMap[c2.ID]
							if c2.X > c.X && c2.In[p.Y].Dir > 16 {
								isPrevSolid = true
							}
						}
					}

					x := c.X*gui.FocusSpacing.X + gui.NationalFocusLink.Position.X + gui.LinkBegin.X + gui.LinkOffsets.X + spacingX

					length := int(math.Abs(float64(c.X-p.X)))*gui.FocusSpacing.Y + gui.LinkBegin.X + gui.LinkOffsets.X + spacingX

					img = LRdash
					if (c.In[p.Y].Dir > 16 || isPrevSolid) && p.Out.Dir > 16 {
						img = LR
						isPrevSolid = true
					}

					for i := 1; i < length/gui.LinkSpacing.X; i++ {
						x += step
						if containsInt(cornerXvalues, x) {
							break
						}
						draw.Draw(dst,
							image.Rectangle{
								image.Point{x, y},
								image.Point{x + img.Bounds().Max.X, y + img.Bounds().Max.Y}},
							img,
							image.ZP,
							draw.Over)
					}
				}

				// Children corner (in).
				a := c.In[p.Y]
				img := a.Get()
				if img != nil && !containsPoint(drawnCoords, image.Point{x, y}) {
					draw.Draw(dst,
						image.Rectangle{
							image.Point{x, y},
							image.Point{x + img.Bounds().Max.X, y + img.Bounds().Max.Y}},
						img,
						image.ZP,
						draw.Over)
				}
				drawnCoords = append(drawnCoords, image.Point{x, y})

				// Children vertical lines (in).
				if c.Y-p.Y > 0 {
					img = UD
					if c.In[p.Y].Dir < 16 {
						img = UDdash
					}

					nextCornerY := maxYinRange(c.In, p.Y)
					childY := c.Y
					if nextCornerY != 0 {
						childY = nextCornerY
					}

					length := (childY-p.Y)*gui.FocusSpacing.Y + gui.LinkEnd.Y - gui.LinkSpacing.Y*2

					if nextCornerY != 0 {
						length += gui.LinkSpacing.Y
					}

					var i int
					for i = 1; i <= length/gui.LinkSpacing.Y; i++ {
						if !containsPoint(drawnCoords, image.Point{x, y + gui.LinkSpacing.Y*i}) {
							draw.Draw(dst,
								image.Rectangle{
									image.Point{x, y + gui.LinkSpacing.Y*i},
									image.Point{x + img.Bounds().Max.X, y + img.Bounds().Max.Y + gui.LinkSpacing.Y*i}},
								img,
								image.ZP,
								draw.Over)
						}
						drawnCoords = append(drawnCoords, image.Point{x, y + gui.LinkSpacing.Y*i})
					}
					leftover := length - (i-1)*gui.LinkSpacing.Y
					if leftover > 0 {
						draw.Draw(dst,
							image.Rectangle{
								image.Point{x, y + gui.LinkSpacing.Y*i},
								image.Point{x + img.Bounds().Max.X, y + leftover + gui.LinkSpacing.Y*i}},
							img,
							image.ZP,
							draw.Over)
					}
				}
			}
		}
	}
	return nil
}

func readTextureAndGetFrames(texture string, frame1, frame2 int) (image.Image, image.Image, error) {
	s := gfxMap[texture]
	err := s.readTexture()
	if err != nil {
		return nil, nil, err
	}
	f1, err := s.getFrame(frame1)
	if err != nil {
		return nil, nil, err
	}
	f2, err := s.getFrame(frame2)
	if err != nil {
		return nil, nil, err
	}
	return f1, f2, nil
}

func (s *SpriteType) readTexture() error {
	imgFile, err := os.Open(s.TextureFile)
	if err != nil {
		// Try looking for the sprite in other declared mod/game folders.
		texture := filepath.Clean(s.TextureFile)
		for _, p := range modPaths {
			base := filepath.Clean(p)
			if strings.HasPrefix(texture, base+string(os.PathSeparator)) {
				texture = strings.TrimPrefix(texture, base)
				break
			}
		}
		texture = strings.TrimLeft(texture, string(os.PathSeparator))

		for i := len(modPaths) - 1; i >= 0; i-- {
			imgFile, err = os.Open(filepath.Join(modPaths[i], texture))
			if err == nil {
				goto TextureFileFound
			}
		}

		return err
	}
TextureFileFound:
	defer imgFile.Close()

	s.Image, _, err = image.Decode(imgFile)
	if err != nil {
		return err
	}
	return nil
}

func (s *SpriteType) getFrame(f int) (image.Image, error) {
	if s.Image == nil {
		return nil, fmt.Errorf("%s has no image data", s.Name)
	}
	if f < 1 {
		return nil, fmt.Errorf("frame number must be higher then 0, it is currently %d", f)
	}
	frames := s.NoOfFrames
	if frames <= 0 {
		frames = 1
	}
	if f > frames {
		return nil, fmt.Errorf("requested frame %d out of %d for %s", f, frames, s.Name)
	}
	frameSize := image.Point{s.Image.Bounds().Dx() / frames, s.Image.Bounds().Dy()}
	if frameSize.X <= 0 || frameSize.Y <= 0 {
		return nil, fmt.Errorf("invalid frame size %dx%d for %s", frameSize.X, frameSize.Y, s.Name)
	}
	dst := image.NewRGBA(image.Rectangle{image.ZP, frameSize})
	draw.Draw(dst, dst.Bounds(), s.Image, image.Point{frameSize.X * (f - 1), 0}, draw.Src)
	return dst, nil
}
